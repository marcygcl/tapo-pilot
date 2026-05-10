"""
collect.py — Recolección de datos via TP-Link Cloud API.

Funciona desde cualquier red — no requiere estar en la misma Wi-Fi que los plugs.

Uso local (loop continuo):
    uv run python scripts/collect.py

Uso via GitHub Actions (una sola lectura):
    uv run python scripts/collect.py --once
"""

import asyncio
import csv
import os
import sys
import signal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from rich.console import Console
from rich.table import Table

from utils import (
    TAPO_EMAIL, TAPO_PASSWORD, DEVICES, POLL_INTERVAL,
    classify_intensity, now_bogota, is_school_hours, log_path
)

console = Console()
running = True

CSV_FIELDS = [
    "timestamp", "alias", "aula", "colegio",
    "is_on", "watts", "intensity", "today_wh", "month_wh",
    "runtime_today_min", "school_hours"
]


def handle_exit(sig, frame):
    global running
    console.print("\n[yellow]Deteniendo recolección...[/yellow]")
    running = False


signal.signal(signal.SIGINT,  handle_exit)
signal.signal(signal.SIGTERM, handle_exit)


async def get_cloud_devices() -> dict:
    """Conecta a la nube de TP-Link y retorna dict {alias: device}."""
    from tplinkcloud import TPLinkDeviceManager
    manager = TPLinkDeviceManager(TAPO_EMAIL, TAPO_PASSWORD, include_kasa=False)
    all_devices = await manager.get_devices()
    return {d.get_alias(): d for d in all_devices}


async def read_plug(alias: str, cfg: dict, cloud_device) -> dict | None:
    """Lee el estado actual de un plug via cloud."""
    try:
        info  = await cloud_device.get_sys_info()
        is_on = info.get("relay_state", 0) == 1

        watts     = 0.0
        today_wh  = 0.0
        month_wh  = 0.0

        if hasattr(cloud_device, "get_emeter_realtime"):
            emeter = await cloud_device.get_emeter_realtime()
            watts  = round(emeter.get("power", 0), 1)

        if hasattr(cloud_device, "get_emeter_daily"):
            today  = now_bogota()
            daily  = await cloud_device.get_emeter_daily(year=today.year, month=today.month)
            today_wh = round(daily.get(str(today.day), 0), 1)

        if hasattr(cloud_device, "get_emeter_monthly"):
            today    = now_bogota()
            monthly  = await cloud_device.get_emeter_monthly(year=today.year)
            month_wh = round(monthly.get(str(today.month), 0), 1)

        return {
            "timestamp":         now_bogota().isoformat(timespec="seconds"),
            "alias":             alias,
            "aula":              cfg["aula"],
            "colegio":           cfg["colegio"],
            "is_on":             int(is_on),
            "watts":             watts,
            "intensity":         classify_intensity(watts),
            "today_wh":          today_wh,
            "month_wh":          month_wh,
            "runtime_today_min": info.get("on_time", 0) // 60,
            "school_hours":      int(is_school_hours()),
        }
    except Exception as e:
        console.print(f"  [red]Error leyendo {alias}:[/red] {e}")
        return None


def append_to_csv(row: dict):
    path = log_path()
    file_exists = os.path.isfile(path)
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def make_status_table(readings: list[dict | None]) -> Table:
    t = Table(title=f"Última lectura — {now_bogota().strftime('%Y-%m-%d %H:%M:%S')} (Bogotá)")
    t.add_column("Aula",       style="bold")
    t.add_column("Colegio")
    t.add_column("Estado")
    t.add_column("Watts",      justify="right")
    t.add_column("Intensidad")
    t.add_column("Wh hoy",    justify="right")
    for r in readings:
        if r is None:
            t.add_row("—", "—", "[red]ERROR[/red]", "—", "—", "—")
            continue
        on_str = "[green]ON[/green]" if r["is_on"] else "[red]OFF[/red]"
        t.add_row(r["aula"], r["colegio"], on_str, str(r["watts"]), r["intensity"], str(r["today_wh"]))
    return t


async def poll_once() -> list[dict | None]:
    try:
        cloud_devices = await get_cloud_devices()
    except Exception as e:
        console.print(f"[red]Error conectando a la nube TP-Link:[/red] {e}")
        return [None] * len(DEVICES)

    tasks = []
    for alias, cfg in DEVICES.items():
        dev = cloud_devices.get(alias)
        if dev is None:
            console.print(f"[yellow]'{alias}' no encontrado en la nube — verificar alias en app Tapo[/yellow]")
            tasks.append(asyncio.sleep(0))  # placeholder
        else:
            tasks.append(read_plug(alias, cfg, dev))

    return await asyncio.gather(*tasks)


async def main():
    console.rule("[bold]Smart Plug Collection — Cloud Mode — Piloto Bogotá 2026[/bold]")

    if not TAPO_EMAIL or not TAPO_PASSWORD:
        console.print("[red]Faltan TAPO_EMAIL o TAPO_PASSWORD en .env[/red]")
        sys.exit(1)

    if not DEVICES:
        console.print("[red]No hay dispositivos en DEVICES (scripts/utils.py)[/red]")
        sys.exit(1)

    # ── Modo single-shot (GitHub Actions) ──────────────────────────────────
    if "--once" in sys.argv:
        console.print(f"Modo single-shot · {len(DEVICES)} plugs")
        readings = await poll_once()
        for row in readings:
            if row:
                append_to_csv(row)
        console.print(make_status_table(readings))
        saved = sum(1 for r in readings if r)
        console.print(f"[green]✓ {saved}/{len(DEVICES)} lecturas guardadas → {log_path()}[/green]")
        return

    # ── Modo continuo (local) ───────────────────────────────────────────────
    console.print(
        f"Monitoreando [bold]{len(DEVICES)} plugs[/bold] via nube TP-Link · "
        f"Intervalo: {POLL_INTERVAL}s · Detener con Ctrl+C\n"
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
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            await asyncio.sleep(1)

    console.print("[bold green]Recolección detenida.[/bold green]")


if __name__ == "__main__":
    asyncio.run(main())
