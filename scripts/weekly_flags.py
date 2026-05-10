"""
weekly_flags.py — Analiza los CSVs de la semana y genera lista de banderas.

Uso:
    python scripts/weekly_flags.py              # semana actual
    python scripts/weekly_flags.py 2026-05-05   # semana del lunes indicado

Una aula recibe bandera si el filtro estuvo OFF durante más de FLAG_THRESHOLD_DAYS
días escolares completos en la semana (registros dentro del horario escolar).
"""

import sys
import os
import glob
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
from rich.console import Console
from rich.table import Table

from utils import TIMEZONE, FLAG_THRESHOLD_DAYS, now_bogota

console = Console()
LOGS_DIR = Path(__file__).parent.parent / "energy_logs"


def get_week_dates(anchor: datetime | None = None) -> list[str]:
    """Retorna las fechas lunes–viernes de la semana del anchor (o semana actual)."""
    anchor = anchor or now_bogota()
    monday = anchor - timedelta(days=anchor.weekday())
    return [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(5)]


def load_week(dates: list[str]) -> pd.DataFrame | None:
    """Carga todos los CSVs de la semana en un solo DataFrame."""
    frames = []
    for date in dates:
        path = LOGS_DIR / f"{date}.csv"
        if path.exists():
            df = pd.read_csv(path, parse_dates=["timestamp"])
            frames.append(df)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def analyze(df: pd.DataFrame) -> pd.DataFrame:
    """
    Por cada aula, calcula:
    - días escolares con al menos 1 lectura ON durante horario escolar
    - días escolares con CERO lecturas ON → día completamente apagado
    - bandera si días_apagados > FLAG_THRESHOLD_DAYS
    """
    # Solo lecturas dentro del horario escolar
    school = df[df["school_hours"] == 1].copy()

    if school.empty:
        return pd.DataFrame()

    school["date"] = school["timestamp"].dt.date

    results = []
    for (aula, colegio), grp in school.groupby(["aula", "colegio"]):
        days_with_data = grp["date"].nunique()
        days_on = grp[grp["is_on"] == 1]["date"].nunique()
        days_off = days_with_data - days_on

        avg_watts = grp[grp["is_on"] == 1]["watts"].mean() if days_on > 0 else 0
        total_wh  = grp["today_wh"].max() if "today_wh" in grp else 0  # approx

        flagged = days_off > FLAG_THRESHOLD_DAYS

        results.append({
            "aula":          aula,
            "colegio":       colegio,
            "días con datos": days_with_data,
            "días ON":       days_on,
            "días OFF":      days_off,
            "watts prom":    round(avg_watts, 1),
            "bandera":       flagged,
        })

    return pd.DataFrame(results).sort_values("días OFF", ascending=False)


def print_report(summary: pd.DataFrame, week_dates: list[str]):
    console.rule(f"[bold]Reporte de Banderas — Semana {week_dates[0]} al {week_dates[-1]}[/bold]")

    flagged   = summary[summary["bandera"]]
    ok        = summary[~summary["bandera"]]

    console.print(f"\nTotal aulas analizadas: [bold]{len(summary)}[/bold]")
    console.print(f"Aulas con bandera:       [bold red]{len(flagged)}[/bold red]")
    console.print(f"Aulas sin bandera:       [bold green]{len(ok)}[/bold green]\n")

    if not flagged.empty:
        console.print("[bold red]⚑ Aulas que requieren visita:[/bold red]")
        t = Table(show_header=True, header_style="bold red")
        for col in ["aula", "colegio", "días ON", "días OFF", "watts prom"]:
            t.add_column(col)
        for _, row in flagged.iterrows():
            t.add_row(
                row["aula"], row["colegio"],
                str(row["días ON"]), str(row["días OFF"]),
                f"{row['watts prom']}W"
            )
        console.print(t)
        console.print()

    if not ok.empty:
        console.print("[bold green]✓ Aulas dentro del umbral:[/bold green]")
        t2 = Table(show_header=True, header_style="bold green")
        for col in ["aula", "colegio", "días ON", "días OFF", "watts prom"]:
            t2.add_column(col)
        for _, row in ok.iterrows():
            t2.add_row(
                row["aula"], row["colegio"],
                str(row["días ON"]), str(row["días OFF"]),
                f"{row['watts prom']}W"
            )
        console.print(t2)

    # Guardar CSV de banderas
    out_path = LOGS_DIR / f"flags_{week_dates[0]}.csv"
    summary.to_csv(out_path, index=False)
    console.print(f"\n[dim]Reporte guardado en: {out_path}[/dim]")


def main():
    anchor = None
    if len(sys.argv) > 1:
        try:
            anchor = datetime.strptime(sys.argv[1], "%Y-%m-%d").replace(tzinfo=TIMEZONE)
        except ValueError:
            console.print(f"[red]Fecha inválida:[/red] {sys.argv[1]} — usar formato YYYY-MM-DD")
            sys.exit(1)

    week_dates = get_week_dates(anchor)
    console.print(f"Analizando semana: {' · '.join(week_dates)}")

    df = load_week(week_dates)
    if df is None or df.empty:
        console.print("[yellow]No se encontraron datos para esta semana.[/yellow]")
        sys.exit(0)

    summary = analyze(df)
    if summary.empty:
        console.print("[yellow]No hay datos dentro del horario escolar esta semana.[/yellow]")
        sys.exit(0)

    print_report(summary, week_dates)


if __name__ == "__main__":
    main()
