from __future__ import annotations

import numpy as np
import pandas as pd


def moving_average(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def weekly_close(prices: pd.Series) -> pd.Series:
    clean = prices.dropna().sort_index().astype(float)
    if clean.empty:
        return clean
    return clean.resample("W-FRI").last().dropna()


def weekly_moving_averages(prices: pd.Series, windows: list[int]) -> dict[int, float]:
    weekly = weekly_close(prices)
    if weekly.empty:
        return {window: np.nan for window in windows}
    return {window: moving_average(weekly, window).iloc[-1] for window in windows}


def rsi_components(series: pd.Series, period: int = 14) -> pd.DataFrame:
    clean = series.dropna().sort_index().astype(float)
    frame = pd.DataFrame(index=clean.index)
    frame["fechamento"] = clean
    frame["variacao"] = clean.diff()
    frame["ganho"] = frame["variacao"].clip(lower=0)
    frame["perda"] = -frame["variacao"].clip(upper=0)
    frame["media_ganho_wilder"] = np.nan
    frame["media_perda_wilder"] = np.nan
    frame["rs"] = np.nan
    frame["rsi"] = np.nan
    if len(frame) <= period:
        return frame

    first_pos = period
    avg_gain = frame["ganho"].iloc[1 : period + 1].mean()
    avg_loss = frame["perda"].iloc[1 : period + 1].mean()
    frame.iloc[first_pos, frame.columns.get_loc("media_ganho_wilder")] = avg_gain
    frame.iloc[first_pos, frame.columns.get_loc("media_perda_wilder")] = avg_loss

    for i in range(first_pos + 1, len(frame)):
        avg_gain = ((avg_gain * (period - 1)) + frame["ganho"].iloc[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + frame["perda"].iloc[i]) / period
        frame.iloc[i, frame.columns.get_loc("media_ganho_wilder")] = avg_gain
        frame.iloc[i, frame.columns.get_loc("media_perda_wilder")] = avg_loss

    valid = frame["media_ganho_wilder"].notna() & frame["media_perda_wilder"].notna()
    frame.loc[valid & frame["media_perda_wilder"].eq(0) & frame["media_ganho_wilder"].gt(0), "rsi"] = 100.0
    frame.loc[valid & frame["media_ganho_wilder"].eq(0) & frame["media_perda_wilder"].gt(0), "rsi"] = 0.0
    regular = valid & frame["media_perda_wilder"].gt(0)
    frame.loc[regular, "rs"] = frame.loc[regular, "media_ganho_wilder"] / frame.loc[regular, "media_perda_wilder"]
    frame.loc[regular, "rsi"] = 100 - (100 / (1 + frame.loc[regular, "rs"]))
    flat = valid & frame["media_ganho_wilder"].eq(0) & frame["media_perda_wilder"].eq(0)
    frame.loc[flat, "rsi"] = 50.0
    return frame


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    components = rsi_components(series, period)
    return components["rsi"].reindex(series.index)


def bollinger_bands(series: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    middle = series.rolling(period, min_periods=period).mean()
    std = series.rolling(period, min_periods=period).std(ddof=0)
    upper = middle + num_std * std
    lower = middle - num_std * std
    position = (series - lower) / (upper - lower)
    return pd.DataFrame(
        {
            "bollinger_upper": upper,
            "bollinger_middle": middle,
            "bollinger_lower": lower,
            "bollinger_position": position,
        }
    )


def ytd_return(series: pd.Series) -> float:
    clean = series.dropna()
    if clean.empty:
        return np.nan
    year = clean[clean.index.year == clean.index[-1].year]
    if len(year) < 2:
        return np.nan
    return (year.iloc[-1] / year.iloc[0]) - 1


def classify_rsi(value: float) -> tuple[str, str]:
    if pd.isna(value):
        return "indisponivel", "RSI ausente"
    if value < 30:
        return "sobrevenda", "RSI abaixo de 30; fraqueza sem confirmacao"
    if value < 50:
        return "zona fraca", ""
    if value < 65:
        return "zona favoravel", ""
    if value <= 70:
        return "favoravel com atencao", ""
    return "sobrecompra", "RSI acima de 70"


def classify_trend(price: float, ma9: float, ma21: float, ma50: float, ma100: float) -> str:
    values = [price, ma9, ma21, ma50, ma100]
    if any(pd.isna(v) for v in values):
        return "dados insuficientes"
    if ma9 < ma21 and ma50 < ma100 and price < ma50:
        return "Descarte"
    if ma9 > ma21 and ma50 > ma100 and price > ma50:
        return "Forte alta"
    if ma9 > ma21 and price > ma50:
        return "Aceitavel"
    if ma9 < ma21 or price < ma50:
        return "Fraca"
    return "Neutra"


def classify_bollinger(price: float, middle: float, upper: float, lower: float, trend: str, rsi_value: float) -> tuple[str, str]:
    if any(pd.isna(v) for v in [price, middle, upper, lower]):
        return "dados insuficientes", "Bollinger ausente"
    width = upper - lower
    if width <= 0:
        return "dados insuficientes", "Bandas sem amplitude"
    distance_upper = abs(upper - price) / width
    distance_lower = abs(price - lower) / width
    positive_trend = trend in {"Forte alta", "Aceitavel"}
    negative_trend = trend in {"Fraca", "Descarte"}
    if price < lower:
        return "rompendo banda inferior", "Preco abaixo da banda inferior"
    if price > middle and price < upper and not (rsi_value > 70 and distance_upper < 0.15):
        return "favoravel", ""
    if distance_lower < 0.2 and positive_trend:
        return "oportunidade", ""
    if distance_upper < 0.15 and rsi_value > 70:
        return "sobrecompra", "Preco proximo da banda superior com RSI alto"
    if price < middle and negative_trend:
        return "alerta negativo", "Preco abaixo da media central e tendencia negativa"
    return "neutra", ""


def calculate_technical_snapshot(prices: pd.Series, settings: dict) -> dict:
    windows = settings["technical"]["moving_averages_weekly"]
    weekly = weekly_close(prices)
    ma = weekly_moving_averages(prices, windows)
    current_price = weekly.iloc[-1] if not weekly.empty else np.nan
    last_close_date = weekly.index[-1] if not weekly.empty else pd.NaT
    rsi_series = rsi(weekly, settings["technical"]["rsi_period"])
    rsi_value = rsi_series.iloc[-1] if not rsi_series.empty else np.nan
    bands_frame = bollinger_bands(weekly, settings["technical"]["bollinger_period"], settings["technical"]["bollinger_std"])
    bands = bands_frame.iloc[-1] if not bands_frame.empty else pd.Series(dtype=float)
    trend = classify_trend(current_price, ma.get(9), ma.get(21), ma.get(50), ma.get(100))
    rsi_status, rsi_alert = classify_rsi(rsi_value)
    boll_status, boll_alert = classify_bollinger(
        current_price,
        bands.get("bollinger_middle", np.nan),
        bands.get("bollinger_upper", np.nan),
        bands.get("bollinger_lower", np.nan),
        trend,
        rsi_value,
    )
    recovery = ma.get(9, np.nan) > ma.get(21, np.nan) and current_price > ma.get(50, np.nan) and rsi_value > 50
    return {
        "timeframe_tecnico": "1W",
        "fonte_fechamento": "fechamento semanal W-FRI sobre serie de precos carregada",
        "data_ultimo_fechamento": last_close_date,
        "fechamento_usado": current_price,
        "preco_atual": current_price,
        "mm9": ma.get(9, np.nan),
        "mm21": ma.get(21, np.nan),
        "mm50": ma.get(50, np.nan),
        "mm100": ma.get(100, np.nan),
        "rsi": rsi_value,
        "rsi_periodos": settings["technical"]["rsi_period"],
        "rsi_timeframe": "1W",
        "rsi_status": rsi_status,
        "bollinger_upper": bands.get("bollinger_upper", np.nan),
        "bollinger_middle": bands.get("bollinger_middle", np.nan),
        "bollinger_lower": bands.get("bollinger_lower", np.nan),
        "bollinger_position": bands.get("bollinger_position", np.nan),
        "bollinger_periodos": settings["technical"]["bollinger_period"],
        "bollinger_std": settings["technical"]["bollinger_std"],
        "bollinger_timeframe": "1W",
        "bollinger_status": boll_status,
        "tendencia": trend,
        "retorno_ytd": ytd_return(prices),
        "recuperacao_forte": bool(recovery),
        "alertas_tecnicos": "; ".join(a for a in [rsi_alert, boll_alert] if a),
    }