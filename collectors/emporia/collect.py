"""Collector Emporia (Vue / smart outlets) — vía la API cloud `pyemvue`.

Lee el estado on/off y los watts en tiempo real de TODOS los dispositivos de la
cuenta, junto con la energía acumulada del día (Wh), y los guarda en
energy_logs/emporia/YYYY-MM-DD.csv.

Uso:
    uv run python collectors/emporia/collect.py          # loop cada POLL_INTERVAL
    uv run python collectors/emporia/collect.py --once    # una lectura (GitHub Actions)

Requiere en el .env:
    EMPORIA_EMAIL=...
    EMPORIA_PASSWORD=...
"""

import argparse
import csv
import datetime
import os
import signal
import sys
import time as _time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # collectors/ -> common
from rich.console import Console
from rich.table import Table

from pyemvue import PyEmVue
from pyemvue.enums import Scale, Unit

from common import (
    EMPORIA_DEVICES,
    EMPORIA_EMAIL,
    EMPORIA_PASSWORD,
    POLL_INTERVAL,
    is_school_hours,
    log_path,
    now_bogota,
)

COLLECTOR = "emporia"
console = Console()
running = True
CSV_FIELDS = ["timestamp", "alias", "is_on", "watts", "today_wh", "school_hours"]

# Cachea los tokens para no re-autenticar en cada corrida.
TOKEN_FILE = str(Path(__file__).resolve().parent / ".emporia_tokens.json")

# El canal "1,2,3" (Main) es el total del dispositivo en la API de Emporia.
MAIN_CHANNEL = "1,2,3"


def handle_exit(sig, frame):
    global running
    console.print("\n[yellow]Deteniendo...[/yellow]")
    running = False


signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)


def _channel(usage_device):
    """Devuelve el canal Main del dispositivo, o None si no hay datos."""
    if not usage_device or not usage_device.channels:
        return None
    return usage_device.channels.get(MAIN_CHANNEL) or next(
        iter(usage_device.channels.values()), None
    )


def watts_from(usage_device):
    """Convierte el consumo en escala 1S (kWh en ese segundo) a watts.

    1 kWh en 1 s == 3600 kW de potencia media (3600 s por hora); x1000 -> W.
    """
    ch = _channel(usage_device)
    return round((ch.usage or 0.0) * 3600 * 1000, 1) if ch else 0.0


def wh_from(usage_device):
    """Energía del día en Wh a partir de la escala DAY (kWh) -> x1000."""
    ch = _channel(usage_device)
    return round((ch.usage or 0.0) * 1000, 1) if ch else 0.0


def connect():
    vue = PyEmVue()
    vue.login(
        username=EMPORIA_EMAIL,
        password=EMPORIA_PASSWORD,
        token_storage_file=TOKEN_FILE,
    )
    return vue


def read_all(vue):
    """Una lectura de todos los dispositivos: on/off, watts y Wh del día."""
    devices = vue.get_devices()
    gids = [d.device_gid for d in devices]
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    watt_usage = vue.get_device_list_usage(
        deviceGids=gids, instant=now_utc, scale=Scale.SECOND.value, unit=Unit.KWH.value
    )
    day_usage = vue.get_device_list_usage(
        deviceGids=gids, instant=now_utc, scale=Scale.DAY.value, unit=Unit.KWH.value
    )
    outlet_on = {o.device_gid: o.outlet_on for o in vue.get_outlets()}

    ts = now_bogota().isoformat(timespec="seconds")
    school = int(is_school_hours())
    rows = []
    for d in devices:
        gid = d.device_gid
        cfg = EMPORIA_DEVICES.get(gid, {})
        alias = cfg.get("alias") or d.device_name or str(gid)

        if gid in outlet_on:
            is_on = int(bool(outlet_on[gid]))
        elif d.outlet is not None:
            is_on = int(bool(d.outlet.outlet_on))
        else:
            is_on = ""  # monitor sin relé: no aplica

        rows.append({
            "timestamp": ts,
            "alias": alias,
            "is_on": is_on,
            "watts": watts_from(watt_usage.get(gid)),
            "today_wh": wh_from(day_usage.get(gid)),
            "school_hours": school,
        })
    return rows


def append_csv(rows):
    path = log_path(COLLECTOR)
    exists = os.path.isfile(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not exists:
            w.writeheader()
        w.writerows(rows)


def make_table(rows):
    t = Table(title=f"Emporia — {now_bogota().strftime('%Y-%m-%d %H:%M:%S')} (Bogotá)")
    t.add_column("Alias", style="bold")
    t.add_column("Estado")
    t.add_column("Watts", justify="right")
    t.add_column("Wh hoy", justify="right")
    for r in rows:
        if r["is_on"] == "":
            estado = "[dim]N/A[/dim]"
        elif r["is_on"]:
            estado = "[green]ON[/green]"
        else:
            estado = "[red]OFF[/red]"
        t.add_row(r["alias"], estado, str(r["watts"]), str(r["today_wh"]))
    return t


def poll_once(vue):
    rows = read_all(vue)
    append_csv(rows)
    console.print(make_table(rows))
    return rows


def main(once=False):
    console.rule("[bold]Emporia Collector[/bold]")
    if not EMPORIA_EMAIL or not EMPORIA_PASSWORD:
        console.print("[red]Faltan credenciales EMPORIA_EMAIL / EMPORIA_PASSWORD[/red]")
        sys.exit(1)

    try:
        vue = connect()
    except Exception as e:
        console.print(f"[red]Error de login:[/red] {e}")
        sys.exit(1)

    if once:
        poll_once(vue)
        return

    console.print(f"Monitoreando dispositivos Emporia · intervalo {POLL_INTERVAL}s · Ctrl+C para detener\n")
    count = 0
    while running:
        count += 1
        console.print(f"[dim]Lectura #{count}...[/dim]")
        try:
            poll_once(vue)
        except Exception as e:
            console.print(f"  [red]Error en lectura:[/red] {e}")
            try:
                vue = connect()  # re-autentica si el token expiró
            except Exception as e2:
                console.print(f"  [red]Re-login falló:[/red] {e2}")
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            _time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collector Emporia")
    parser.add_argument("--once", action="store_true", help="Una sola lectura y salir (CI)")
    args = parser.parse_args()
    main(once=args.once)
