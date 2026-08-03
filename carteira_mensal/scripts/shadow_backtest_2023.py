from __future__ import annotations

from pathlib import Path

import shadow_backtest_2025 as backtest


ROOT = Path(__file__).resolve().parents[1]

backtest.MONTHS_2025 = {
    f"2023-{month:02d}": f"carteira_historica_2023_{month:02d}.xlsx"
    for month in range(1, 13)
}
backtest.OUTPUT_FILE = ROOT / "output" / "excel" / "shadow_backtest_2023.xlsx"
backtest.LOG_FILE = ROOT / "output" / "excel" / "shadow_backtest_2023.log"


if __name__ == "__main__":
    backtest.main()
