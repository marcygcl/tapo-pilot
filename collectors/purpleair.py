"""Collector PurpleAir (calidad del aire / PM2.5) — vía la API REST de PurpleAir.

Independiente del resto de collectors de enchufes: pollea un conjunto fijo de
sensores PurpleAir cada 5 minutos y guarda sus lecturas en
energy_logs/purpleair/YYYY-MM-DD.csv (un archivo por día).

Sensores monitoreados:
    308614 -> Aula 19 (con filtro Sqair)
    308572 -> Aula sin filtro (referencia)

Campos del CSV:
    timestamp, sensor_id, sensor_name, pm25, temperature, humidity

Uso:
    uv run python collectors/purpleair.py          # loop continuo cada 5 min
    uv run python collectors/purpleair.py --once     # una sola lectura y salir

Requiere en el .env (raíz del repo):
    PURPLEAIR_API_KEY=...        # read key de https://develop.purpleair.com

API (PurpleAir REST v1):
    GET https://api.purpleair.com/v1/sensors/{sensor_index}?fields=name,pm2.5,temperature,humidity
    Header: X-API-Key: {key}
    Nota: temperature viene en °F y humidity en % (lecturas crudas del sensor).
"""

import argparse
import csv
import json
import os
import signal
import sys
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

console = Console()
running = True

# Configuración propia (no depende de config.json para mantener el módulo aislado).
API_KEY = os.getenv("PURPLEAIR_API_KEY")
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "America/Bogota"))
POLL_INTERVAL = 300  # 5 minutos, fijo para este collector

API_URL = "https://api.purpleair.com/v1/sensors"
API_FIELDS = "name,pm2.5,temperature,humidity"

# Sensores a pollear: sensor_index -> etiqueta de referencia.
SENSORS = {
    308614: "Aula 19 (con filtro)",
    308572: "Aula sin filtro",
}

CSV_FIELDS = ["timestamp", "sensor_id", "sensor_name", "pm25", "temperature", "humidity"]


def handle_exit(sig, frame):
    global running
    console.print("\n[yellow]Deteniendo...[/yellow]")
    running = False


signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)


def now_local():
    return datetime.now(tz=TIMEZONE)


def log_path(date=None):
    """Ruta del CSV diario: energy_logs/purpleair/YYYY-MM-DD.csv."""
    d = (date or now_local()).strftime("%Y-%m-%d")
    logs_dir = ROOT / "energy_logs" / "purpleair"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return str(logs_dir / f"{d}.csv")


def fetch_sensor(sensor_id):
    """Lee un sensor de la API de PurpleAir. Devuelve el dict `sensor` o lanza."""
    url = f"{API_URL}/{sensor_id}?" + urllib.parse.urlencode({"fields": API_FIELDS})
    req = urllib.request.Request(url, headers={"X-API-Key": API_KEY})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    return payload.get("sensor") or {}


def read_all():
    """Una lectura de todos los sensores -> filas para el CSV."""
    ts = now_local().isoformat(timespec="seconds")
    rows = []
    for sensor_id, ref in SENSORS.items():
        try:
            s = fetch_sensor(sensor_id)
        except urllib.error.HTTPError as e:
            console.print(f"  [red]HTTP {e.code} en sensor {sensor_id} ({ref}):[/red] {e.reason}")
            continue
        except Exception as e:
            console.print(f"  [red]Error leyendo sensor {sensor_id} ({ref}):[/red] {e}")
            continue

        rows.append({
            "timestamp": ts,
            "sensor_id": sensor_id,
            "sensor_name": s.get("name") or ref,
            "pm25": s.get("pm2.5", ""),
            "temperature": s.get("temperature", ""),
            "humidity": s.get("humidity", ""),
        })
    return rows


def append_csv(rows):
    path = log_path()
    exists = os.path.isfile(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not exists:
            w.writeheader()
        w.writerows(rows)


def make_table(rows):
    t = Table(title=f"PurpleAir — {now_local().strftime('%Y-%m-%d %H:%M:%S')} (Bogotá)")
    t.add_column("Sensor", style="bold")
    t.add_column("ID")
    t.add_column("PM2.5", justify="right")
    t.add_column("Temp °F", justify="right")
    t.add_column("Humedad %", justify="right")
    for r in rows:
        t.add_row(
            str(r["sensor_name"]),
            str(r["sensor_id"]),
            str(r["pm25"]),
            str(r["temperature"]),
            str(r["humidity"]),
        )
    return t


def poll_once():
    rows = read_all()
    if rows:
        append_csv(rows)
        console.print(make_table(rows))
    else:
        console.print("  [yellow]Sin lecturas en este ciclo[/yellow]")
    return rows


def main(once=False):
    console.rule("[bold]PurpleAir Collector[/bold]")
    if not API_KEY:
        console.print("[red]Falta PURPLEAIR_API_KEY en el .env[/red]")
        sys.exit(1)

    if once:
        try:
            poll_once()
        except Exception as e:
            console.print(f"[red]Error en lectura:[/red] {e}")
            sys.exit(1)
        return

    console.print(f"Monitoreando {len(SENSORS)} sensores PurpleAir · intervalo {POLL_INTERVAL}s · Ctrl+C para detener\n")
    count = 0
    while running:
        count += 1
        console.print(f"[dim]Lectura #{count}...[/dim]")
        try:
            poll_once()
        except Exception as e:
            console.print(f"  [red]Error en lectura:[/red] {e}")
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            _time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collector PurpleAir (PM2.5)")
    parser.add_argument("--once", action="store_true", help="Una sola lectura y salir")
    args = parser.parse_args()
    main(once=args.once)
