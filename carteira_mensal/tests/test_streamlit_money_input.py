import pytest

from app.streamlit_app_user import format_brl_amount, parse_brl_amount


@pytest.mark.parametrize(
    ('text', 'expected'),
    [('10.000,00', 10000.0), ('10000,00', 10000.0), ('R$ 1.500,25', 1500.25), ('1.000', 1000.0)],
)
def test_parse_brl_amount(text, expected):
    assert parse_brl_amount(text) == expected


@pytest.mark.parametrize('text', ['999,99', '10,000.00', '1.00,00', '-1000', 'nan', ''])
def test_parse_brl_amount_rejects_invalid_values(text):
    with pytest.raises(ValueError):
        parse_brl_amount(text)


def test_format_brl_amount():
    assert format_brl_amount(10000.0) == '10.000,00'
    assert format_brl_amount(1234567.89) == '1.234.567,89'
