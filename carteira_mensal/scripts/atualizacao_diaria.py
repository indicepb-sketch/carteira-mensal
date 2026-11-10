from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from production_registry import activate_month, load_active
import perfil as profiles

EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"


def _scoped(base: Path, perfil: str | None) -> Path:
    key = profiles.normalize(perfil)
    return base if key == profiles.DEFAULT_PROFILE else base / key


def _excel(perfil: str | None = None) -> Path:
    return _scoped(EXCEL_DIR, perfil)


def _production(perfil: str | None = None) -> Path:
    return _scoped(ROOT / "output" / "production", perfil)


def _pdf(perfil: str | None = None) -> Path:
    return _scoped(ROOT / "output" / "pdf", perfil)


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


def latest_forward(month: str, perfil: str | None = None) -> Path | None:
    year, mon = month_parts(month)
    files = [
        p
        for p in _excel(perfil).glob(f"carteira_forward_{year:04d}_{mon:02d}*.xlsx")
        if "verificacao_falhou" not in p.name
    ]
    return sorted(files, key=lambda p: p.stat().st_mtime)[-1] if files else None


def run_command(args: list[str]) -> None:
    print("$", " ".join(args))
    subprocess.run(args, cwd=ROOT, check=True)


def previous_month(month: str) -> str:
    year, mon = month_parts(month)
    return f"{year - 1:04d}-12" if mon == 1 else f"{year:04d}-{mon - 1:02d}"


def last_weekday_of(month: str) -> str:
    year, mon = month_parts(month)
    end = pd.Timestamp(year=year, month=mon, day=1) + pd.offsets.MonthEnd(0)
    return pd.bdate_range(end.replace(day=1), end)[-1].date().isoformat()


def previous_month_is_closed(month: str, perfil: str | None = None) -> bool:
    """True se a parcial mais recente do mes ja esta marcada como fechamento_mes."""
    key = month.replace("-", "_")
    for path in sorted(_excel(perfil).glob(f"parcial_carteira_forward_{key}_v*.xlsx"), reverse=True):
        try:
            frame = pd.read_excel(path, sheet_name="Resumo Parcial")
            values = dict(zip(frame["metrica"].astype(str), frame["valor"]))
        except (OSError, ValueError, KeyError):
            continue
        if str(values.get("status")) == "fechamento_mes":
            return True
    return False


def close_previous_month(month: str, perfil: str | None = None) -> None:
    """Fecha o mes anterior quando cotacoes e CDI do ultimo dia util ja estiverem disponiveis.

    Falhas aqui nao interrompem a atualizacao do mes corrente: o mes anterior segue
    parcial e a nova tentativa ocorre no proximo agendamento.
    """
    previous = previous_month(month)
    if not (_production(perfil) / f"{previous}.json").exists():
        return
    if previous_month_is_closed(previous, perfil):
        return
    try:
        run_command([
            sys.executable, "scripts/forward_partial.py", "--mes", previous, "--perfil", profiles.normalize(perfil),
            "--data-avaliacao", last_weekday_of(previous), "--allow-network", "--cdi-auto",
        ])
    except subprocess.CalledProcessError as exc:
        print(f"[aviso] fechamento de {previous} nao concluido ({exc}); nova tentativa no proximo agendamento.")


def ensure_monthly_report(month: str, perfil: str | None = None) -> None:
    """Gera o PDF do mes anterior depois de fechado, se ainda nao existir.

    Falhas nao interrompem a atualizacao diaria; nova tentativa no proximo agendamento.
    """
    previous = previous_month(month)
    pdf = _pdf(perfil) / f"carteira_recomendada_{previous.replace('-', '_')}.pdf"
    if pdf.exists() or not previous_month_is_closed(previous, perfil):
        return
    try:
        run_command([sys.executable, "scripts/gerar_relatorio_mensal.py", "--mes", previous, "--perfil", profiles.normalize(perfil)])
    except subprocess.CalledProcessError as exc:
        print(f"[aviso] relatorio PDF de {previous} nao gerado ({exc}); nova tentativa no proximo agendamento.")


def update_profile(month: str, perfil: str, force_forward: bool) -> Path:
    """Forma (se faltar), ativa e atualiza o parcial da carteira de um perfil."""
    active_path = _production(perfil) / "active.json"
    active_forward, active = load_active(ROOT, perfil) if active_path.exists() else (None, None)
    if active and month == active["month"]:
        if force_forward:
            raise SystemExit("Mes oficial ja ativado; nao e permitido substituir a carteira com --force-forward.")
        forward = active_forward
    else:
        forward = latest_forward(month, perfil)
        if force_forward or forward is None:
            run_command([sys.executable, "scripts/forward_test.py", "--mes", month, "--perfil", perfil])
            forward = latest_forward(month, perfil)

    if forward is None:
        raise SystemExit(f"Nenhum arquivo forward encontrado/gerado para {month} (perfil {perfil}).")

    if active is None or month > active["month"]:
        activate_month(month, forward, ROOT, perfil)

    close_previous_month(month, perfil)
    ensure_monthly_report(month, perfil)

    partial_cmd = [sys.executable, "scripts/forward_partial.py", "--mes", month, "--perfil", perfil, "--arquivo", str(forward),
                   "--allow-network", "--cdi-auto"]
    run_command(partial_cmd)
    print(f"Atualizacao diaria concluida para {month} (perfil {perfil}).")
    print(f"Forward usado: {forward.name}")
    return forward


def main() -> None:
    args = parse_args()
    if not args.allow_network:
        raise SystemExit("Parcial oficial exige --allow-network para cotacoes atuais e CDI; nenhuma atualizacao foi iniciada.")
    month = args.mes or current_month()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for perfil in profiles.profiles_for_month(month, ROOT):
        update_profile(month, perfil, args.force_forward)


if __name__ == "__main__":
    main()
