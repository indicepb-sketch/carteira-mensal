from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Atualiza a carteira forward/parcial usada pela plataforma."
    )
    parser.add_argument("--mes", default=None, help="Mes no formato YYYY-MM. Default: mes atual.")
    parser.add_argument(
        "--force-forward",
        action="store_true",
        help="Forca nova formacao da carteira forward mesmo se ja existir arquivo do mes.",
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="Permite consultas online para precos e CDI no calculo da parcial.",
    )
    return parser.parse_args()


def current_month() -> str:
    now = datetime.now(ZoneInfo("America/Fortaleza"))
    return f"{now.year:04d}-{now.month:02d}"


def month_parts(month: str) -> tuple[int, int]:
    year_s, month_s = month.split("-")
    return int(year_s), int(month_s)


def latest_forward(month: str) -> Path | None:
    year, mon = month_parts(month)
    files = [
        p
        for p in EXCEL_DIR.glob(f"carteira_forward_{year:04d}_{mon:02d}*.xlsx")
        if "verificacao_falhou" not in p.name
    ]
    return sorted(files, key=lambda p: p.stat().st_mtime)[-1] if files else None


def run_command(args: list[str]) -> None:
    print("$", " ".join(args))
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> None:
    args = parse_args()
    month = args.mes or current_month()
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    forward = latest_forward(month)
    if args.force_forward or forward is None:
        run_command([sys.executable, "scripts/forward_test.py", "--mes", month])
        forward = latest_forward(month)

    if forward is None:
        raise SystemExit(f"Nenhum arquivo forward encontrado/gerado para {month}.")

    partial_cmd = [sys.executable, "scripts/forward_partial.py", "--mes", month, "--arquivo", str(forward)]
    if args.allow_network:
        partial_cmd.extend(["--allow-network", "--cdi-auto"])
    run_command(partial_cmd)

    print(f"Atualizacao diaria concluida para {month}.")
    print(f"Forward usado: {forward.name}")


if __name__ == "__main__":
    main()
