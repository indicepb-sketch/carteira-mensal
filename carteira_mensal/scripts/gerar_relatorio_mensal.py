from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from relatorio_mensal import build_report  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera o PDF mensal da Carteira Recomendada.")
    parser.add_argument("--mes", required=True, help="Mes no formato YYYY-MM")
    parser.add_argument("--saida", default=None, help="Caminho do PDF (padrao: output/pdf/carteira_recomendada_YYYY_MM.pdf)")
    parser.add_argument("--perfil", default="moderado", help="Perfil da carteira (moderado ou arrojado). Padrao: moderado.")
    parser.add_argument("--offline", action="store_true", help="Usa apenas o cache de precos em data/report_cache")
    args = parser.parse_args()
    path = build_report(args.mes, Path(args.saida) if args.saida else None, allow_network=not args.offline, perfil=args.perfil)
    print(f"Relatorio gerado: {path}")


if __name__ == "__main__":
    main()
