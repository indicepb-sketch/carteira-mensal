"""Parcial do mes aberto: pregoes ainda sem CDI oficial usam a ultima taxa diaria conhecida."""
import pandas as pd

from scripts.forward_partial import extend_cdi_with_last_rate


def _cdi(*dates):
    return pd.DataFrame({"data": pd.to_datetime(list(dates)), "cdi_diario_pct": 0.05, "cdi_diario_decimal": 0.0005})


def test_missing_cdi_days_are_filled_with_the_last_official_rate_and_flagged():
    cdi = _cdi("2026-11-11", "2026-11-12")
    trading = pd.DatetimeIndex(pd.to_datetime(["2026-11-11", "2026-11-12", "2026-11-13"]))
    out, estimated = extend_cdi_with_last_rate(cdi, trading, pd.Timestamp("2026-11-13"))
    assert estimated == 1 and out["data"].max() == pd.Timestamp("2026-11-13")
    assert out["estimado"].tolist() == [False, False, True] and out["cdi_diario_decimal"].iloc[-1] == 0.0005


def test_nothing_is_estimated_when_cdi_is_current_or_the_gap_is_too_long():
    cdi = _cdi("2026-11-12")
    same = pd.DatetimeIndex(pd.to_datetime(["2026-11-12"]))
    assert extend_cdi_with_last_rate(cdi, same, pd.Timestamp("2026-11-12"))[1] == 0
    long_gap = pd.DatetimeIndex(pd.bdate_range("2026-11-13", periods=8))
    out, estimated = extend_cdi_with_last_rate(cdi, long_gap, long_gap.max())
    assert estimated == 0 and not out["estimado"].any()


def test_non_trading_days_do_not_accrue_cdi():
    cdi = _cdi("2026-11-12")
    trading = pd.DatetimeIndex(pd.to_datetime(["2026-11-12", "2026-11-16"]))       # 13 e 14 sem pregao
    out, estimated = extend_cdi_with_last_rate(cdi, trading, pd.Timestamp("2026-11-16"))
    assert estimated == 1 and out["data"].tolist()[-1] == pd.Timestamp("2026-11-16")
