from __future__ import annotations

from pathlib import Path

import shadow_backtest_2025 as backtest


ROOT = Path(__file__).resolve().parents[1]

backtest.MONTHS_2025 = {
    f"2022-{month:02d}": f"carteira_historica_2022_{month:02d}.xlsx"
    for month in range(1, 13)
}
backtest.OUTPUT_FILE = ROOT / "output" / "excel" / "shadow_backtest_2022.xlsx"
backtest.LOG_FILE = ROOT / "output" / "excel" / "shadow_backtest_2022.log"


if __name__ == "__main__":
    backtest.main()
