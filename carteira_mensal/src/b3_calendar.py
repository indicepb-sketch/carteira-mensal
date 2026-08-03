from __future__ import annotations

from datetime import date

import pandas as pd


def _easter_date(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def b3_holidays(year: int) -> set[pd.Timestamp]:
    easter = pd.Timestamp(_easter_date(year))
    fixed = [
        (1, 1),    # Confraternizacao Universal
        (1, 25),   # Aniversario de Sao Paulo, sede da B3
        (4, 21),   # Tiradentes
        (5, 1),    # Dia do Trabalho
        (9, 7),    # Independencia
        (10, 12),  # Nossa Senhora Aparecida
        (11, 2),   # Finados
        (11, 15),  # Proclamacao da Republica
        (11, 20),  # Consciencia Negra
        (12, 24),  # Vespera de Natal, sem pregao regular
        (12, 25),  # Natal
        (12, 31),  # Ultimo dia do ano, sem pregao regular
    ]
    movable = [
        easter - pd.Timedelta(days=48),  # Carnaval, segunda
        easter - pd.Timedelta(days=47),  # Carnaval, terca
        easter - pd.Timedelta(days=2),   # Sexta-feira Santa
        easter + pd.Timedelta(days=60),  # Corpus Christi
    ]
    holidays = {pd.Timestamp(year=year, month=month, day=day).normalize() for month, day in fixed}
    holidays.update(day.normalize() for day in movable)
    return holidays


def b3_trading_days_by_rule(year: int, month: int) -> pd.DatetimeIndex:
    start = pd.Timestamp(year=year, month=month, day=1)
    end = start + pd.offsets.MonthEnd(0)
    weekdays = pd.bdate_range(start, end)
    holidays = b3_holidays(year)
    return pd.DatetimeIndex([day.normalize() for day in weekdays if day.normalize() not in holidays])


def trading_days_from_market_data(index_prices: pd.DataFrame, year: int, month: int, benchmark: str) -> pd.DatetimeIndex:
    if index_prices is None or index_prices.empty or benchmark not in index_prices:
        return pd.DatetimeIndex([])
    start = pd.Timestamp(year=year, month=month, day=1)
    end = start + pd.offsets.MonthEnd(0)
    series = index_prices[benchmark].dropna()
    days = series.loc[(series.index >= start) & (series.index <= end)].index
    return pd.DatetimeIndex(pd.to_datetime(days).normalize().unique()).sort_values()


def resolve_b3_trading_days(settings: dict, index_prices: pd.DataFrame, year: int, month: int) -> tuple[pd.DatetimeIndex, str, str]:
    calendar = settings.get("calendar", {})
    fallback = int(calendar.get("fallback_trading_days_month", 21))
    benchmark = settings.get("data", {}).get("indexes", {}).get("IBOV", "^BVSP")
    month_end = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
    days = trading_days_from_market_data(index_prices, year, month, benchmark)
    if len(days) > 0 and pd.Timestamp(days[-1]).normalize() >= month_end.normalize():
        return days, "B3 via serie historica do IBOV (^BVSP)", "pregoes_observados_no_benchmark_mes_completo"
    rule_days = b3_trading_days_by_rule(year, month)
    if len(rule_days) > 0:
        status = "calendario_b3_por_regras_mes_completo"
        if len(days) > 0:
            status += "; serie_ibov_parcial_usada_apenas_para_precos"
        return rule_days, "B3 calendario por regras locais", status
    if len(days) > 0:
        return days, "B3 via serie historica do IBOV (^BVSP)", "fallback_serie_ibov_parcial"
    first = pd.Timestamp(year=year, month=month, day=1)
    return pd.bdate_range(first, periods=fallback), "fallback 21 dias uteis", "fallback_21_pregoes"


def first_b3_trading_day(settings: dict, index_prices: pd.DataFrame, year: int, month: int) -> tuple[pd.Timestamp, str, str]:
    days, source, status = resolve_b3_trading_days(settings, index_prices, year, month)
    return pd.Timestamp(days[0]).normalize(), source, status

