from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest

from app import streamlit_app_user as app


def test_upcoming_formation_uses_actual_first_trading_day():
    assert app.upcoming_formation("2026-11-03", date(2026, 10, 31))
    assert app.upcoming_formation("2026-11-03", date(2026, 11, 2))
    assert not app.upcoming_formation("2026-11-03", date(2026, 11, 3))


def test_home_shows_allocation_and_actionable_plan():
    page = Path(__file__).resolve().parents[1] / "app" / "streamlit_app_user.py"
    view = AppTest.from_file(str(page)).run(timeout=90)

    assert not view.exception
    assert view.metric[0].label.startswith("Ações · ")
    assert view.metric[1].label.startswith("CDI / reserva · ")
    assert list(view.dataframe[0].value.columns[:4]) == [
        "Ativo", "Quantidade", "Valor planejado", "Peso"
    ]
    assert app.pct(0.2813) == "28,13%"
