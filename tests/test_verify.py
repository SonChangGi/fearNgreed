from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest

from fearngreed.verify import (
    _verify_cross_artifact_consistency,
    _verify_etf_price_contract,
    _verify_history,
    _verify_history_channel_roles,
    _verify_scatter_state_boundaries,
    _verify_strategy_comparison,
    verify_local,
)


def test_verify_history_accepts_columnar_and_rejects_width_mismatch() -> None:
    history = {
        "dataAsOf": "2026-07-15",
        "numericPrecisionDigits": 8,
        "seriesEncoding": "columnar-v1",
        "seriesColumns": ["date", "value"],
        "seriesRows": [["2026-07-14", 1], ["2026-07-15", 2]],
    }
    _verify_history(history)
    history["seriesRows"][1] = ["2026-07-15"]
    with pytest.raises(ValueError, match="width mismatch"):
        _verify_history(history)


def test_verify_history_requires_v5_numeric_precision_contract() -> None:
    history = {
        "dataAsOf": "2026-07-15",
        "numericPrecisionDigits": 10,
        "seriesEncoding": "columnar-v1",
        "seriesColumns": ["date"],
        "seriesRows": [["2026-07-15"]],
    }
    with pytest.raises(ValueError, match="numeric precision contract"):
        _verify_history(history)


def test_verify_local_reports_hashes_and_headroom() -> None:
    root = Path(__file__).resolve().parents[1]
    summary = json.loads((root / "data" / "summary.json").read_text(encoding="utf-8"))
    schema = json.loads((root / "schemas" / "summary.schema.json").read_text(encoding="utf-8"))
    if summary.get("methodologyVersion") != schema["properties"]["methodologyVersion"].get("const"):
        pytest.skip("generated data is updated after the v5 pipeline integration step")
    receipt = verify_local(root, minimum_headroom_ratio=0)
    assert receipt["ok"] is True
    assert set(receipt["hashes"]) == {
        "data/summary.json",
        "data/dashboard.json",
        "data/history.json",
        "data/automation-status.json",
        "data/strategy-comparison.json",
    }


def _actual_policy_fixture(
    pair_id: str, leverage: int, long_ticker: str, inverse_ticker: str, policy_id: str
) -> dict:
    inverse_exposure = 0.0 if policy_id == "long_cash" else 0.2
    metrics = {
        "implementation": "actual_listed_etfs",
        "longExposure": 0.25,
        "inverseExposure": inverse_exposure,
        "cashExposure": 0.75 - inverse_exposure,
        "shortExposure": 0.0,
        "shortTradeCount": 0,
        "inverseTradeCount": 0 if policy_id == "long_cash" else 1,
    }
    return {
        "pair": {
            "pairId": pair_id,
            "leverage": leverage,
            "longTicker": long_ticker,
            "inverseTicker": inverse_ticker,
            "implementation": "actual_listed_etfs",
        },
        "oneWayCostBps": 10,
        "policyId": policy_id,
        "position": "cash",
        "latestInstrumentTicker": None,
        "longExitPercentile": 80,
        "inverseExitPercentile": 20,
        "status": "ok",
        "unavailableReason": None,
        "metrics": metrics,
        "trades": [],
        "equity": [
            {
                "date": "2020-01-02",
                "position": "cash",
                "instrumentTicker": None,
            }
        ],
        "range": {
            "appliedStartDate": "2020-01-02",
            "appliedEndDate": "2026-07-16",
        },
        "calculationSource": "python_verified_actual_etfs",
    }


def _strategy_contract_fixtures() -> tuple[dict, dict, dict, dict]:
    summary = {"payload": {"strategyComparisonUrl": "./strategy-comparison.json"}}
    dashboard = {"backtests": {"proxies": {}}}
    history = {
        "strategyScenario": {
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
            "configurableInputs": {
                "lookback": {"default": 252, "minimum": 60, "maximum": 756, "step": 1},
                "minimumR2": {
                    "default": 0.2,
                    "minimum": 0,
                    "maximum": 0.8,
                    "step": 0.05,
                },
                "extremeTail": {
                    "default": 5,
                    "minimum": 1,
                    "maximum": 20,
                    "step": 1,
                },
                "maxHolding": {
                    "default": 20,
                    "minimum": 1,
                    "maximum": 60,
                    "step": 1,
                },
            },
            "minimumTrainingObservationsFormula": (
                "min(lookback,max(40,min(200,ceil(lookback*0.8))))"
            ),
            "pastOnly": True,
            "evaluationRangeSeparate": True,
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
            "inverseExitFormula": "100-longExitPercentile",
            "calculationLocation": ("browser_on_server_published_history_and_adjusted_prices"),
            "regressionRefit": True,
            "signalEngineVersion": "browser-past-only-rolling-v1",
            "scenarioAuthority": "browser_user_scenario_not_canonical_server_output",
            "configurableInputs": deepcopy(history["strategyScenario"]["configurableInputs"]),
            "minimumTrainingObservationsFormula": (
                "min(lookback,max(40,min(200,ceil(lookback*0.8))))"
            ),
            "pastOnly": True,
            "evaluationRangeSeparate": True,
        },
        "policyDefinitions": {
            "longInverseCash": {
                "policyId": "long_inverse_cash",
                "role": "canonical_actual_listed_etf_research",
                "positionAccounting": "positive_listed_etf_units_no_synthetic_short",
                "shortExposure": 0,
                "borrowRequired": False,
            },
            "longShortCash": {
                "policyId": "long_short_cash",
                "role": "legacy_diagnostic_backward_compatibility",
                "borrowFeeAnnualPct": 0,
                "shortabilityModeled": False,
            },
        },
        "legacyProxyContract": {
            "canonical": False,
            "role": "legacy_diagnostic_backward_compatibility",
            "implementation": "synthetic_short_or_single_long_proxy",
        },
        "proxies": {},
    }
    actual_pairs = {}
    for pair_id, leverage, long_ticker, inverse_ticker in (
        ("1x", 1, "069500", "114800"),
        ("2x", 2, "122630", "252670"),
    ):
        policies = {
            policy_id: _actual_policy_fixture(
                pair_id, leverage, long_ticker, inverse_ticker, policy_id
            )
            for policy_id in ("long_cash", "long_inverse_cash")
        }
        actual_pairs[pair_id] = {
            "pair": dict(policies["long_cash"]["pair"]),
            "status": "ok",
            "reason": None,
            "policies": policies,
            "fullPeriodMetrics": {
                policy_id: {
                    field: result.get(field)
                    for field in (
                        "status",
                        "unavailableReason",
                        "position",
                        "latestInstrumentTicker",
                        "metrics",
                        "range",
                    )
                }
                for policy_id, result in policies.items()
            },
        }
    strategy["actualEtfPairs"] = {
        "authority": "canonical_server_verified_actual_etfs",
        "canonical": True,
        "calculationSource": "python_verified_actual_etfs",
        "implementation": "positive_units_in_listed_long_and_inverse_etfs",
        "oneWayCostBps": 10,
        "longExitPercentile": 80,
        "inverseExitPercentile": 20,
        "commonPeriod": {
            "basis": "four_etf_common_adjusted_price_sessions",
            "tickers": ["069500", "114800", "122630", "252670"],
            "status": "ok",
            "reason": None,
            "start": "2020-01-02",
            "end": "2026-07-16",
            "sessionCount": 1600,
        },
        "pairs": actual_pairs,
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

    summary, dashboard, history, strategy = _strategy_contract_fixtures()
    strategy["dynamicExitControl"]["pastOnly"] = False
    with pytest.raises(ValueError, match="dynamic exit-control contract"):
        _verify_strategy_comparison(summary, dashboard, history, strategy)


def test_strategy_verifier_allows_optional_226490_legacy_proxy_failure() -> None:
    summary, dashboard, history, strategy = _strategy_contract_fixtures()
    strategy["proxies"].pop("226490")
    dashboard["backtests"]["proxies"].pop("226490")

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
        "primaryEntities": [{"position": "cash", "primaryProxy": "069500"}],
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


def test_etf_price_contract_requires_all_actual_pair_fields_and_crosschecks() -> None:
    tickers = ("226490", "069500", "114800", "122630", "252670")
    columns = [
        "date",
        *[field for ticker in tickers for field in (f"p{ticker}Open", f"p{ticker}Close")],
    ]
    history = {
        "seriesEncoding": "columnar-v1",
        "seriesColumns": columns,
        "seriesRows": [["2026-07-16", *range(100, 100 + len(columns) - 1)]],
    }
    dashboard = {
        "crosschecks": {
            "etf": {
                ticker: {
                    "state": "ok",
                    "historyReconciliation": {"state": "ok", "unresolvedCount": 0},
                }
                for ticker in tickers
            }
        }
    }
    summary = {"status": {"degradedReasons": []}}

    _verify_etf_price_contract(summary, dashboard, history)

    history["seriesColumns"].remove("p252670Close")
    history["seriesRows"][0].pop()
    with pytest.raises(ValueError, match="252670 ETF history price fields are missing"):
        _verify_etf_price_contract(summary, dashboard, history)


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
