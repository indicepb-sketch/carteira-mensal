"""Reconstrucao do preco nominal (o que foi cotado na epoca) a partir do fechamento ajustado so por desdobramentos e grupamentos."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import precos_nominais as pn  # noqa: E402

DAYS = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"])


def test_reverse_split_divides_the_old_adjusted_price_back_to_what_was_quoted():
    adjusted = pd.Series([100.0, 100.0, 10.0, 10.0], index=DAYS)             # grupamento de 10 por 1 em 06/01: o historico antigo vem multiplicado por 10
    nominal = pn.nominal_from_split_adjusted(adjusted, pd.Series([0.1], index=pd.to_datetime(["2020-01-06"])))
    assert nominal.tolist() == pytest.approx([10.0, 10.0, 10.0, 10.0])


def test_split_multiplies_the_old_adjusted_price_back_to_what_was_quoted():
    adjusted = pd.Series([5.0, 5.0, 5.0, 5.0], index=DAYS)                   # desdobramento de 2 por 1 em 06/01: o historico antigo vem dividido por 2
    nominal = pn.nominal_from_split_adjusted(adjusted, pd.Series([2.0], index=pd.to_datetime(["2020-01-06"])))
    assert nominal.tolist() == pytest.approx([10.0, 10.0, 5.0, 5.0])


def test_two_events_compound_and_the_event_day_itself_is_not_changed():
    adjusted = pd.Series([200.0, 20.0, 20.0, 20.0], index=DAYS)
    splits = pd.Series([0.1, 2.0], index=pd.to_datetime(["2020-01-03", "2020-01-07"]))
    nominal = pn.nominal_from_split_adjusted(adjusted, splits)
    assert nominal.tolist() == pytest.approx([40.0, 40.0, 40.0, 20.0])       # antes de 03/01 x0,1 x2; de 03/01 a 06/01 x2; depois de 07/01 sem ajuste


def test_without_events_the_price_is_unchanged():
    adjusted = pd.Series([1.0, 2.0, 3.0, 4.0], index=DAYS)
    assert pn.nominal_from_split_adjusted(adjusted, pd.Series(dtype=float)).tolist() == adjusted.tolist()


def test_versioned_nominal_series_covers_the_universe_and_is_close_to_adjusted_in_recent_months():
    nominal, adjusted = pn.load_nominal(), pd.read_csv(pn.ADJUSTED_CSV, index_col=0, parse_dates=True)
    assert set(adjusted.columns).issubset(set(nominal.columns))
    last = pd.Timestamp("2026-10-30")
    ratio = (nominal.loc[:last].iloc[-1] / adjusted.loc[:last].iloc[-1]).dropna()
    assert ratio.between(0.97, 1.05).all()          # no dia da formacao nao ha evento nem dividendo posterior: nominal ~ ajustado
    assert float(nominal["PETR4.SA"].loc[:"2020-01-02"].iloc[-1]) == pytest.approx(30.7, abs=0.5)      # PETR4 custava cerca de R$ 30 em jan/2020
