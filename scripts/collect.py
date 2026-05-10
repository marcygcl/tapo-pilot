"""
collect.py — Loop de recolección de datos de los Tapo P115.

Uso:
    python scripts/collect.py

Guarda una fila por lectura en energy_logs/YYYY-MM-DD.csv.
Corre indefinidamente hasta que lo detengas con Ctrl+C.
"""

import asyncio
import csv
import os
import sys
import signal
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from rich.console import Console
from rich.live import Live
from rich.table import Table

from utils import (
    TAPO_EMAIL, TAPO_PASSWORD, DEVICES, POLL_INTERVAL,
    classify_intensity, now_bogota, is_school_hours, log_path
)

console = Console()
running = True

CSV_FIELDS = [
    "timestamp", "alias", "aula", "colegio", "ip",
    "is_on", "watts", "intensity", "today_wh", "month_wh",
    "runtime_today_min", "school_hours"
]


def handle_exit(sig, frame):
    global running
    console.print("\n[yellow]Deteniendo recolección...[/yellow]")
    running = False


signal.signal(signal.SIGINT,  handle_exit)
signal.signal(signal.SIGTERM, handle_exit)


async def read_plug(alias: str, cfg: dict) -> dict | None:
    """Lee el estado actual de un plug y retorna un dict con todos los campos."""
    try:
        from tapo import ApiClient
        client = ApiClient(TAPO_EMAIL, TAPO_PASSWORD)
        device = await client.p115(cfg["ip"])
        info   = await device.get_device_info()
        usage  = await device.get_energy_usage()

        watts = round(usage.current_power / 1000, 1)   # mW → W
        return {
            "timestamp":        now_bogota().isoformat(timespec="seconds"),
            "alias":            alias,
            "aula":             cfg["aula"],
            "colegio":          cfg["colegio"],
            "ip":               cfg["ip"],
            "is_on":            int(info.device_on),
            "watts":            watts,
            "intensity":        classify_intensity(watts),
            "today_wh":         round(usage.today_energy / 1000, 1),
            "month_wh":         round(usage.month_energy / 1000, 1),
            "runtime_today_min":usage.today_runtime,
            "school_hours":     int(is_school_hours()),
        }
    except Exception as e:
        console.print(f"  [red]Error leyendo {alias} ({cfg['ip']}):[/red] {e}")
        return None


def append_to_csv(row: dict):
    """Agrega una fila al CSV del día actual (lo crea si no existe)."""
    path = log_path()
    file_exists = os.path.isfile(path)
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def make_status_table(readings: list[dict | None]) -> Table:
    """Construye una tabla Rich con el estado actual de todos los plugs."""
    t = Table(title=f"Última lectura — {now_bogota().strftime('%Y-%m-%d %H:%M:%S')} (Bogotá)")
    t.add_column("Aula",        style="bold")
    t.add_column("Colegio")
    t.add_column("Estado")
    t.add_column("Watts", justify="right")
    t.add_column("Intensidad")
    t.add_column("Wh hoy", justify="right")
    t.add_column("Runtime (min)", justify="right")

    for r in readings:
        if r is None:
            t.add_row("—", "—", "[red]ERROR[/red]", "—", "—", "—", "—")
            continue
        on_str = "[green]ON[/green]" if r["is_on"] else "[red]OFF[/red]"
        t.add_row(
            r["aula"], r["colegio"], on_str,
            str(r["watts"]), r["intensity"],
            str(r["today_wh"]), str(r["runtime_today_min"])
        )
    return t


async def poll_once() -> list[dict | None]:
    """Lee todos los plugs en paralelo."""
    tasks = [read_plug(alias, cfg) for alias, cfg in DEVICES.items()]
    return await asyncio.gather(*tasks)


async def main():
    console.rule("[bold]Smart Plug Data Collection — Piloto Bogotá 2026[/bold]")

    if not DEVICES:
        console.print(
            "[red]No hay dispositivos configurados.[/red]\n"
            "Corre primero: [bold]python scripts/setup_plugs.py[/bold]"
        )
        sys.exit(1)

    console.print(
        f"Monitoreando [bold]{len(DEVICES)} plugs[/bold] · "
        f"Intervalo: {POLL_INTERVAL}s · "
        f"Logs en: energy_logs/\n"
        f"Detener con [bold]Ctrl+C[/bold]\n"
    )

    poll_count = 0
    while running:
        poll_count += 1
        console.print(f"[dim]Lectura #{poll_count}...[/dim]")

        readings = await poll_once()

        for row in readings:
            if row:
                append_to_csv(row)

        console.print(make_status_table(readings))
        console.print(f"[dim]Próxima lectura en {POLL_INTERVAL}s[/dim]\n")

        # Esperar el intervalo, pero revisar `running` cada segundo
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            await asyncio.sleep(1)

    console.print("[bold green]Recolección detenida. Datos guardados en energy_logs/[/bold green]")


if __name__ == "__main__":
    asyncio.run(main())
