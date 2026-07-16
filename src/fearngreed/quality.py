from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
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
        if critical:
            self.state = "unavailable"
        elif self.state == "ok":
            self.state = "degraded"


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
    if "individual_net_purchase" not in flow.columns:
        report.add("flow_contract_missing_columns", critical=True)
        return report
    closes = pd.to_numeric(kospi["close"], errors="coerce")
    opens = pd.to_numeric(kospi["open"], errors="coerce")
    turnover = pd.to_numeric(kospi["trading_value"], errors="coerce")
    individual_flow = pd.to_numeric(flow["individual_net_purchase"], errors="coerce")
    invalid_price = ((closes <= 0) | (opens <= 0) | closes.isna() | opens.isna()).sum()
    invalid_turnover = ((turnover <= 0) | turnover.isna()).sum()
    invalid_flow = individual_flow.isna().sum()
    report.metrics["invalidPriceRows"] = int(invalid_price)
    report.metrics["invalidTurnoverRows"] = int(invalid_turnover)
    report.metrics["invalidFlowRows"] = int(invalid_flow)
    if invalid_price:
        report.add("invalid_krx_price", critical=True)
    if invalid_turnover:
        report.add("invalid_krx_trading_value", critical=True)
    if invalid_flow:
        report.add("invalid_individual_flow", critical=True)
    kospi_dates = _normalized_dates(kospi.index)
    flow_dates = _normalized_dates(flow.index)
    common = kospi_dates.intersection(flow_dates)
    report.metrics["commonRows"] = int(len(common))
    report.metrics["sourceCompleteness"] = float(
        len(common) / max(1, len(kospi_dates.union(flow_dates)))
    )
    if len(kospi_dates) and len(flow_dates):
        latest_kospi = kospi_dates.max()
        latest_flow = flow_dates.max()
        report.metrics["latestKospiDate"] = latest_kospi.date().isoformat()
        report.metrics["latestFlowDate"] = latest_flow.date().isoformat()
        report.metrics["earliestKospiDate"] = kospi_dates.min().date().isoformat()
        report.metrics["earliestFlowDate"] = flow_dates.min().date().isoformat()
        report.metrics["latestSourceDateMatches"] = bool(latest_kospi == latest_flow)
        if latest_kospi != latest_flow:
            report.add("latest_source_date_mismatch", critical=True)

        kospi_only = kospi_dates.difference(flow_dates)
        flow_only = flow_dates.difference(kospi_dates)
        report.metrics["kospiOnlyDateCount"] = int(len(kospi_only))
        report.metrics["flowOnlyDateCount"] = int(len(flow_only))
        report.metrics["sourceGapCount"] = int(len(kospi_only) + len(flow_only))
        if len(kospi_only) or len(flow_only):
            report.add("source_date_gaps", critical=True)
    if len(common) < 200:
        report.add("insufficient_common_history", critical=True)
    elif report.metrics["sourceCompleteness"] < 0.9:
        report.add("low_source_date_overlap")
    return report


def compare_latest_close(
    primary: pd.Series,
    secondary: pd.Series,
    *,
    tolerance: float = 0.005,
    expected_date: date | str | pd.Timestamp | None = None,
) -> dict[str, Any]:
    left = _normalized_series(primary)
    right = _normalized_series(secondary)
    if left is None or right is None:
        return _unavailable_crosscheck(
            "duplicate_dates",
            expected_date=expected_date,
            primary_date=_latest_date(left),
            secondary_date=_latest_date(right),
            tolerance=tolerance,
        )
    if left.empty or right.empty:
        return _unavailable_crosscheck(
            "missing_prices",
            expected_date=expected_date,
            primary_date=_latest_date(left),
            secondary_date=_latest_date(right),
            tolerance=tolerance,
        )
    expected = (
        _normalized_expected_date(expected_date)
        if expected_date is not None
        else left.index.max()
    )
    common = left.index.intersection(right.index)
    primary_latest = left.index.max()
    secondary_latest = right.index.max()
    if primary_latest != expected or expected not in common or common.max() != expected:
        return _unavailable_crosscheck(
            "expected_date_not_common",
            expected_date=expected,
            primary_date=primary_latest,
            secondary_date=secondary_latest,
            tolerance=tolerance,
        )
    primary_close = float(left.loc[expected])
    secondary_close = float(right.loc[expected])
    if primary_close <= 0 or secondary_close <= 0:
        return _unavailable_crosscheck(
            "invalid_close",
            expected_date=expected,
            primary_date=primary_latest,
            secondary_date=secondary_latest,
            tolerance=tolerance,
        )
    difference = abs(primary_close / secondary_close - 1)
    return {
        "state": "ok" if difference <= tolerance else "mismatch",
        "reason": None if difference <= tolerance else "relative_difference_exceeded",
        "date": expected.date().isoformat(),
        "expectedDate": expected.date().isoformat(),
        "primaryLatestDate": primary_latest.date().isoformat(),
        "secondaryLatestDate": secondary_latest.date().isoformat(),
        "relativeDifference": difference,
        "tolerance": tolerance,
    }


def _normalized_dates(index: pd.Index) -> pd.DatetimeIndex:
    dates = pd.to_datetime(index, errors="coerce", utc=True)
    dates = dates[~dates.isna()].tz_convert(None).normalize()
    return pd.DatetimeIndex(dates.unique()).sort_values()


def _normalized_series(series: pd.Series) -> pd.Series | None:
    values = pd.to_numeric(series, errors="coerce")
    dates = pd.to_datetime(series.index, errors="coerce", utc=True)
    clean = pd.Series(values.to_numpy(), index=dates).dropna()
    clean.index = clean.index.tz_convert(None).normalize()
    if clean.index.duplicated().any():
        return None
    return clean.sort_index()


def _normalized_expected_date(value: date | str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert(None)
    return timestamp.normalize()


def _latest_date(series: pd.Series | None) -> pd.Timestamp | None:
    return None if series is None or series.empty else series.index.max()


def _date_string(value: date | str | pd.Timestamp | None) -> str | None:
    if value is None:
        return None
    return _normalized_expected_date(value).date().isoformat()


def _unavailable_crosscheck(
    reason: str,
    *,
    expected_date: date | str | pd.Timestamp | None,
    primary_date: pd.Timestamp | None,
    secondary_date: pd.Timestamp | None,
    tolerance: float,
) -> dict[str, Any]:
    return {
        "state": "unavailable",
        "reason": reason,
        "date": None,
        "expectedDate": _date_string(expected_date),
        "primaryLatestDate": _date_string(primary_date),
        "secondaryLatestDate": _date_string(secondary_date),
        "relativeDifference": None,
        "tolerance": tolerance,
    }


def _date_or_none(value: Any) -> str | None:
    return value.date().isoformat() if value is not None and not pd.isna(value) else None
