from __future__ import annotations

import pandas as pd

from fearngreed.analysis import align_us_before_krx


def test_us_data_is_strictly_before_each_krx_session() -> None:
    krx_dates = pd.to_datetime(["2026-07-13", "2026-07-14", "2026-07-15"])
    us = pd.Series(
        [100.0, 200.0, 300.0],
        index=pd.to_datetime(["2026-07-12", "2026-07-14", "2026-07-15"]),
    )
    fx = pd.Series(
        [1300.0, 1310.0, 1320.0],
        index=pd.to_datetime(["2026-07-12", "2026-07-14", "2026-07-15"]),
    )
    aligned = align_us_before_krx(krx_dates, us, fx)
    assert aligned.loc["2026-07-14", "mu_close_usd"] == 100
    assert aligned.loc["2026-07-15", "mu_close_usd"] == 200
    assert (aligned["us_session_date"] < aligned.index).all()
    assert (aligned["fx_session_date"] < aligned.index).all()
