from datetime import date

from app import streamlit_app_user as app


def test_upcoming_formation_uses_actual_first_trading_day():
    assert app.upcoming_formation("2026-11-03", date(2026, 10, 31))
    assert app.upcoming_formation("2026-11-03", date(2026, 11, 2))
    assert not app.upcoming_formation("2026-11-03", date(2026, 11, 3))
