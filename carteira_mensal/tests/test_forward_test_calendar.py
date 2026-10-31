from scripts.forward_test import first_business_day, previous_month_end


def test_november_2026_uses_actual_b3_sessions():
    assert previous_month_end(2026, 11).strftime("%Y-%m-%d") == "2026-10-30"
    assert first_business_day(2026, 11).strftime("%Y-%m-%d") == "2026-11-03"
