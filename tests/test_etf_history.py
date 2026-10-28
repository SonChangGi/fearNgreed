from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import pytest

from fearngreed.providers.common import ProviderError
from fearngreed.providers.etf_history import COLUMNS, fetch_etf_history

START, END = date(2020, 1, 1), date(2025, 10, 28)


def frame(start, end):
    index = pd.bdate_range(start, end)
    return pd.DataFrame(
        {column: [float(day.toordinal()) for day in index] for column in COLUMNS}, index=index
    )


def test_pages_resume_after_failure_and_warm_runs_revalidate_recent_data(tmp_path):
    calls = []
    fail = True

    def fetch(ticker, start, end):
        calls.append((start, end))
        if fail and start > START:
            raise ProviderError("sanitized timeout")
        return frame(start, end)

    def run():
        return fetch_etf_history("114800", START, END, cache_dir=tmp_path, fetch_range=fetch)

    with pytest.raises(ProviderError):
        run()
    assert len(list(tmp_path.rglob("*.json"))) == 1
    fail = False
    calls.clear()
    actual = run()
    assert all(start > START for start, _ in calls)
    assert all((end - start).days < 730 for start, end in calls)
    pd.testing.assert_frame_equal(actual, frame(START, END), check_names=False, check_freq=False)
    calls.clear()
    warm = run()
    assert calls == [(END - timedelta(days=14), END)]
    pd.testing.assert_frame_equal(warm, actual, check_names=False)


def test_mutable_failure_preserves_cache_but_cannot_return_cached_success(tmp_path):
    fetch_etf_history(
        "114800", START, END, cache_dir=tmp_path, fetch_range=lambda _, s, e: frame(s, e)
    )
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}

    def fail(*args):
        raise ProviderError("sanitized authentication failure")

    with pytest.raises(ProviderError):
        fetch_etf_history("114800", START, END, cache_dir=tmp_path, fetch_range=fail)
    assert before == {p: p.read_bytes() for p in before}


def test_checksum_corruption_is_refetched(tmp_path):
    fetch_etf_history(
        "114800", START, END, cache_dir=tmp_path, fetch_range=lambda _, s, e: frame(s, e)
    )
    first = sorted(tmp_path.rglob("*.json"))[0]
    payload = json.loads(first.read_text())
    payload["payload"]["rows"][0][1] = 1
    first.write_text(json.dumps(payload))
    calls = []

    def fetch(_, start, end):
        calls.append(start)
        return frame(start, end)

    result = fetch_etf_history("114800", START, END, cache_dir=tmp_path, fetch_range=fetch)
    assert START in calls
    pd.testing.assert_frame_equal(result, frame(START, END), check_names=False, check_freq=False)


@pytest.mark.parametrize("problem", ["duplicate", "nonfinite", "out_of_range", "secret", "empty"])
def test_invalid_page_is_never_checkpointed(tmp_path, problem):
    def fetch(_, start, end):
        result = frame(start, end)
        if problem == "duplicate":
            result = pd.concat([result, result.iloc[:1]])
        elif problem == "nonfinite":
            result.iloc[0, 0] = float("nan")
        elif problem == "out_of_range":
            result.index = pd.DatetimeIndex([day + timedelta(days=900) for day in result.index])
        elif problem == "secret":
            result = result.astype(object)
            result.iloc[0, 0] = "FAKE_KRX_SECRET_CANARY"
        else:
            result = result.iloc[:0]
        return result

    with pytest.raises(ProviderError) as error:
        fetch_etf_history("114800", START, END, cache_dir=tmp_path, fetch_range=fetch)
    assert "CANARY" not in str(error.value)
    assert not list(tmp_path.rglob("*.json"))


def test_historical_replay_does_not_reuse_future_page(tmp_path):
    start = date(2025, 1, 1)
    fetch_etf_history(
        "114800", start, END, cache_dir=tmp_path, fetch_range=lambda _, s, e: frame(s, e)
    )
    past_end = date(2025, 7, 1)
    calls = []

    def fetch(_, first, last):
        calls.append((first, last))
        return frame(first, last)

    past = fetch_etf_history("114800", start, past_end, cache_dir=tmp_path, fetch_range=fetch)
    assert calls == [(start, past_end)]
    assert past.index.max().date() == past_end


def test_extension_replaces_mutable_values_and_preserves_older_rows(tmp_path):
    original = fetch_etf_history(
        "114800", START, END, cache_dir=tmp_path, fetch_range=lambda _, s, e: frame(s, e)
    )
    new_end = END + timedelta(days=1)
    mutable = new_end - timedelta(days=14)
    calls = []

    def fetch(_, start, end):
        calls.append((start, end))
        return frame(start, end) + 1

    result = fetch_etf_history("114800", START, new_end, cache_dir=tmp_path, fetch_range=fetch)
    assert calls == [(mutable, new_end)]
    pd.testing.assert_frame_equal(
        result.loc[: str(mutable - timedelta(days=1))],
        original.loc[: str(mutable - timedelta(days=1))],
        check_names=False,
        check_freq=False,
    )
    assert result.iloc[-1, 0] == new_end.toordinal() + 1


def test_explicit_backfill_revalidates_all_pages(tmp_path):
    fetch_etf_history(
        "114800", START, END, cache_dir=tmp_path, fetch_range=lambda _, s, e: frame(s, e)
    )
    calls = []

    def fetch(_, start, end):
        calls.append(start)
        return frame(start, end) + 1

    actual = fetch_etf_history(
        "114800", START, END, cache_dir=tmp_path, fetch_range=fetch, force_refresh=True
    )
    assert len(calls) == 3 and calls[0] == START
    pd.testing.assert_frame_equal(actual, frame(START, END) + 1, check_freq=False)


def test_empty_short_holiday_tail_does_not_invent_rows(tmp_path):
    start = date(2024, 1, 4)
    # The first 730-day page ends on Friday; its next page is only a weekend.
    end = start + timedelta(days=731)
    actual = fetch_etf_history(
        "114800", start, end, cache_dir=tmp_path, fetch_range=lambda _, s, e: frame(s, e)
    )
    pd.testing.assert_frame_equal(actual, frame(start, end), check_freq=False)
