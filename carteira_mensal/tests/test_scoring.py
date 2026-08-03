import numpy as np
import pandas as pd

from scoring import score_assets, score_fundamentals


def settings():
    return {"risk": {"std_limit_daily": 0.02, "cv_limit": 11.5, "beta_alert": 1.0, "correlation_alert": 0.7}}


def test_score_final_and_missing_fundamentals():
    frame = pd.DataFrame(
        [
            {
                "ticker": "A",
                "mm9": 11,
                "mm21": 10,
                "mm50": 9,
                "mm100": 8,
                "preco_atual": 12,
                "retorno_ytd": 0.2,
                "rsi": 55,
                "bollinger_status": "favoravel",
                "roe": np.nan,
                "roic": 0.16,
                "margem_bruta": np.nan,
                "pl_atual": 8,
                "tendencia_setorial": "alta",
                "retorno_medio": 0.01,
                "desvio_padrao": 0.01,
                "cv": 1,
                "beta": 0.8,
                "correlacao_ibov": 0.5,
            }
        ]
    )
    scored = score_assets(frame, settings())
    assert scored.iloc[0]["nota_final"] > 0
    assert score_fundamentals(frame.iloc[0]) == 10
