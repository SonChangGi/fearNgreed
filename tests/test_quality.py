from __future__ import annotations

from datetime import timedelta

import pandas as pd

from fearngreed.quality import (
    compare_close_anchors,
    compare_latest_close,
    validate_core_inputs,
)


def _core_frames(periods: int = 205) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.bdate_range("2025-01-02", periods=periods)
    kospi = pd.DataFrame(
        {
            "open": range(1000, 1000 + periods),
            "close": range(1001, 1001 + periods),
            "trading_value": [1_000_000.0] * periods,
        },
        index=dates,
    )
    flow = pd.DataFrame(
        {"individual_net_purchase": [10_000.0] * periods},
        index=dates,
    )
    return kospi, flow


def test_core_quality_rejects_latest_source_date_mismatch() -> None:
    kospi, flow = _core_frames()
    flow = flow.iloc[:-1]

    report = validate_core_inputs(kospi, flow)

    assert report.state == "unavailable"
    assert "latest_source_date_mismatch" in report.issues
    assert report.metrics["latestSourceDateMatches"] is False
    assert report.metrics["sourceFreshnessPassed"] is False


def test_core_quality_rejects_internal_source_date_gap_even_with_high_overlap() -> None:
    kospi, flow = _core_frames()
    flow = flow.drop(flow.index[-20])

    report = validate_core_inputs(kospi, flow)

    assert report.metrics["dateOverlapRatio"] > 0.99
    assert report.metrics["sourceCompleteness"] == report.metrics["dateOverlapRatio"]
    assert report.metrics["sourceGapCount"] == 1
    assert report.metrics["sourceSessionCoveragePassed"] is False
    assert report.state == "unavailable"
    assert "source_date_gaps" in report.issues


def test_noncritical_issue_cannot_downgrade_unavailable_to_degraded() -> None:
    kospi, flow = _core_frames(periods=300)
    flow = flow.iloc[-201:-1]

    report = validate_core_inputs(kospi, flow)

    assert "latest_source_date_mismatch" in report.issues
    assert "low_source_date_overlap" in report.issues
    assert report.state == "unavailable"


def test_close_crosscheck_uses_the_explicit_expected_data_date() -> None:
    primary = pd.Series([100.0, 101.0], index=pd.to_datetime(["2026-07-14", "2026-07-15"]))
    secondary = pd.Series([100.0, 101.1], index=pd.to_datetime(["2026-07-14", "2026-07-15"]))

    result = compare_latest_close(primary, secondary, expected_date="2026-07-15")

    assert result["state"] == "ok"
    assert result["date"] == "2026-07-15"
    assert result["expectedDate"] == "2026-07-15"


def test_close_crosscheck_rejects_an_older_common_date() -> None:
    primary = pd.Series([100.0, 101.0], index=pd.to_datetime(["2026-07-14", "2026-07-15"]))
    secondary = pd.Series([100.0], index=pd.to_datetime(["2026-07-14"]))

    result = compare_latest_close(primary, secondary, expected_date="2026-07-15")

    assert result["state"] == "unavailable"
    assert result["reason"] == "expected_date_not_common"
    assert result["date"] is None
    assert result["secondaryLatestDate"] == "2026-07-14"


def test_close_crosscheck_rejects_stale_primary_even_if_expected_is_common() -> None:
    primary = pd.Series([100.0], index=pd.to_datetime(["2026-07-14"]))
    secondary = pd.Series([100.0, 101.0], index=pd.to_datetime(["2026-07-14", "2026-07-15"]))

    result = compare_latest_close(primary, secondary, expected_date="2026-07-15")

    assert result["state"] == "unavailable"
    assert result["reason"] == "expected_date_not_common"


def test_core_quality_reports_source_session_and_freshness_checks() -> None:
    kospi, flow = _core_frames()

    report = validate_core_inputs(
        kospi,
        flow,
        expected_as_of=kospi.index[-1].date() + timedelta(days=5),
        max_freshness_days=3,
    )

    assert report.state == "degraded"
    assert report.metrics["dateOverlapRatio"] == 1.0
    assert report.metrics["flowSessionCoverageRatio"] == 1.0
    assert report.metrics["kospiSessionCoverageRatio"] == 1.0
    assert report.metrics["sourceContractPassed"] is True
    assert report.metrics["sourceSessionCoveragePassed"] is True
    assert report.metrics["sourceFreshnessPassed"] is False
    assert report.metrics["dataFreshnessLagDays"] == 5
    assert "stale_core_sources" in report.issues


def test_historical_close_crosscheck_validates_multiple_anchors() -> None:
    dates = pd.bdate_range("2026-01-02", periods=20)
    primary = pd.Series(range(100, 120), index=dates, dtype=float)
    secondary = primary * 1.001

    result = compare_close_anchors(
        primary,
        secondary,
        expected_date=dates[-1],
        anchor_count=4,
    )

    assert result["state"] == "ok"
    assert result["checkedCount"] == 4
    assert [anchor["date"] for anchor in result["anchors"]][0] == dates[0].date().isoformat()
    assert result["anchors"][-1]["date"] == dates[-1].date().isoformat()


def test_historical_close_crosscheck_fails_when_an_old_anchor_diverges() -> None:
    dates = pd.bdate_range("2026-01-02", periods=20)
    primary = pd.Series(range(100, 120), index=dates, dtype=float)
    secondary = primary.copy()
    secondary.iloc[0] *= 0.9

    result = compare_close_anchors(
        primary,
        secondary,
        expected_date=dates[-1],
        anchor_count=4,
    )

    assert result["state"] == "mismatch"
    assert result["mismatchCount"] == 1
    assert result["reason"] == "historical_anchor_difference_exceeded"


def test_historical_close_crosscheck_requires_multiple_common_sessions() -> None:
    dates = pd.to_datetime(["2026-07-14", "2026-07-15"])
    primary = pd.Series([100.0, 101.0], index=dates)
    secondary = primary.copy()

    result = compare_close_anchors(primary, secondary, expected_date=dates[-1])

    assert result["state"] == "unavailable"
    assert result["reason"] == "insufficient_common_anchors"
