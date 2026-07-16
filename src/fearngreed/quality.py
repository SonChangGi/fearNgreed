from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class QualityReport:
    state: str = "ok"
    issues: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def add(self, issue: str, *, critical: bool = False) -> None:
        if issue not in self.issues:
            self.issues.append(issue)
        self.state = "unavailable" if critical else "degraded"


def validate_core_inputs(kospi: pd.DataFrame, flow: pd.DataFrame) -> QualityReport:
    report = QualityReport()
    report.metrics = {
        "kospiRows": int(len(kospi)),
        "flowRows": int(len(flow)),
        "kospiStart": _date_or_none(kospi.index.min() if len(kospi) else None),
        "kospiEnd": _date_or_none(kospi.index.max() if len(kospi) else None),
        "flowStart": _date_or_none(flow.index.min() if len(flow) else None),
        "flowEnd": _date_or_none(flow.index.max() if len(flow) else None),
    }
    if kospi.empty:
        report.add("missing_krx_kospi", critical=True)
    if flow.empty:
        report.add("missing_pykrx_flow", critical=True)
    if kospi.index.duplicated().any():
        report.add("duplicate_krx_dates", critical=True)
    if flow.index.duplicated().any():
        report.add("duplicate_flow_dates", critical=True)
    required = {"close", "open", "trading_value"}
    if not required.issubset(kospi.columns):
        report.add("krx_contract_missing_columns", critical=True)
        return report
    invalid_price = ((kospi["close"] <= 0) | (kospi["open"] <= 0)).sum()
    invalid_turnover = (kospi["trading_value"] <= 0).sum()
    report.metrics["invalidPriceRows"] = int(invalid_price)
    report.metrics["invalidTurnoverRows"] = int(invalid_turnover)
    if invalid_price:
        report.add("invalid_krx_price", critical=True)
    if invalid_turnover:
        report.add("invalid_krx_trading_value", critical=True)
    common = kospi.index.intersection(flow.index)
    report.metrics["commonRows"] = int(len(common))
    report.metrics["sourceCompleteness"] = float(
        len(common) / max(1, len(kospi.index.union(flow.index)))
    )
    if len(common) < 200:
        report.add("insufficient_common_history", critical=True)
    elif report.metrics["sourceCompleteness"] < 0.9:
        report.add("low_source_date_overlap")
    return report


def compare_latest_close(
    primary: pd.Series, secondary: pd.Series, *, tolerance: float = 0.005
) -> dict[str, Any]:
    left = pd.to_numeric(primary, errors="coerce").dropna()
    right = pd.to_numeric(secondary, errors="coerce").dropna()
    common = left.index.intersection(right.index)
    if common.empty:
        return {"state": "unavailable", "date": None, "relativeDifference": None}
    latest = common.max()
    difference = abs(float(left.loc[latest]) / float(right.loc[latest]) - 1)
    return {
        "state": "ok" if difference <= tolerance else "mismatch",
        "date": latest.date().isoformat(),
        "relativeDifference": difference,
        "tolerance": tolerance,
    }


def _date_or_none(value: Any) -> str | None:
    return value.date().isoformat() if value is not None and not pd.isna(value) else None
