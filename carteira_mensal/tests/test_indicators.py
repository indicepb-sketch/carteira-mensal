import numpy as np
import pandas as pd

from technical_indicators import bollinger_bands, classify_trend, moving_average, rsi, weekly_moving_averages


def test_moving_average():
    series = pd.Series([1, 2, 3, 4, 5])
    result = moving_average(series, 3)
    assert np.isnan(result.iloc[1])
    assert result.iloc[-1] == 4


def test_weekly_moving_averages():
    idx = pd.date_range("2024-01-01", periods=120, freq="D")
    prices = pd.Series(np.arange(120) + 1, index=idx)
    result = weekly_moving_averages(prices, [2, 4])
    assert set(result) == {2, 4}
    assert result[2] > 0


def test_rsi_bounds():
    series = pd.Series(np.linspace(10, 30, 40))
    value = rsi(series, 14).iloc[-1]
    assert 0 <= value <= 100
    assert value > 70


def test_bollinger_bands_population_std():
    series = pd.Series(range(1, 31), dtype=float)
    bands = bollinger_bands(series, period=20, num_std=2)
    window = series.iloc[-20:]
    expected_middle = window.mean()
    expected_upper = expected_middle + 2 * window.std(ddof=0)
    assert np.isclose(bands["bollinger_middle"].iloc[-1], expected_middle)
    assert np.isclose(bands["bollinger_upper"].iloc[-1], expected_upper)


def test_classify_trend_discard():
    assert classify_trend(price=90, ma9=95, ma21=100, ma50=98, ma100=105) == "Descarte"
