from __future__ import annotations

import pandas as pd

from fearngreed.quality import compare_latest_close, validate_core_inputs


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


def test_core_quality_rejects_internal_source_date_gap_even_with_high_overlap() -> None:
    kospi, flow = _core_frames()
    flow = flow.drop(flow.index[-20])

    report = validate_core_inputs(kospi, flow)

    assert report.metrics["sourceCompleteness"] > 0.99
    assert report.metrics["sourceGapCount"] == 1
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
    primary = pd.Series(
        [100.0, 101.0], index=pd.to_datetime(["2026-07-14", "2026-07-15"])
    )
    secondary = pd.Series(
        [100.0, 101.1], index=pd.to_datetime(["2026-07-14", "2026-07-15"])
    )

    result = compare_latest_close(primary, secondary, expected_date="2026-07-15")

    assert result["state"] == "ok"
    assert result["date"] == "2026-07-15"
    assert result["expectedDate"] == "2026-07-15"


def test_close_crosscheck_rejects_an_older_common_date() -> None:
    primary = pd.Series(
        [100.0, 101.0], index=pd.to_datetime(["2026-07-14", "2026-07-15"])
    )
    secondary = pd.Series([100.0], index=pd.to_datetime(["2026-07-14"]))

    result = compare_latest_close(primary, secondary, expected_date="2026-07-15")

    assert result["state"] == "unavailable"
    assert result["reason"] == "expected_date_not_common"
    assert result["date"] is None
    assert result["secondaryLatestDate"] == "2026-07-14"


def test_close_crosscheck_rejects_stale_primary_even_if_expected_is_common() -> None:
    primary = pd.Series([100.0], index=pd.to_datetime(["2026-07-14"]))
    secondary = pd.Series(
        [100.0, 101.0], index=pd.to_datetime(["2026-07-14", "2026-07-15"])
    )

    result = compare_latest_close(primary, secondary, expected_date="2026-07-15")

    assert result["state"] == "unavailable"
    assert result["reason"] == "expected_date_not_common"
