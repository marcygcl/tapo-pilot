"""Collector PurpleAir (calidad del aire / PM2.5) — vía la API REST de PurpleAir.

Independiente del resto de collectors de enchufes: pollea un conjunto fijo de
sensores PurpleAir cada 5 minutos y guarda sus lecturas en
energy_logs/purpleair/YYYY-MM-DD.csv (un archivo por día).

Al arrancar también descarga el HISTÓRICO de ambos sensores (backfill) para
rellenar los CSVs retroactivamente: desde el último timestamp guardado, o los
últimos 7 días si no hay datos locales.

Sensores monitoreados:
    308614 -> Aula 19 (con filtro Sqair)
    308572 -> Aula sin filtro (referencia)

Campos del CSV:
    timestamp, sensor_id, sensor_name, pm25, temperature, humidity

Uso:
    uv run python collectors/purpleair.py               # backfill + loop continuo (5 min)
    uv run python collectors/purpleair.py --once         # backfill + una lectura y salir
    uv run python collectors/purpleair.py --no-backfill  # omite el histórico
    uv run python collectors/purpleair.py --backfill-only  # solo histórico, sin lectura live

API (PurpleAir REST v1) — https://api.purpleair.com/
    Lectura actual:
        GET /v1/sensors/{sensor_index}?fields=name,pm2.5,temperature,humidity
    Histórico (rango):
        GET /v1/sensors/{sensor_index}/history?start_timestamp=X&end_timestamp=Y&average=10&fields=pm2.5_atm,temperature,humidity
        - start/end en epoch UTC (segundos). average en minutos: 0,10,30,60,360,1440
          (no hay promedio de 5 min en el histórico; el mínimo es 10).
        - Límite de rango por request según el promedio: real-time=2d, 10min=3d,
          30min=7d, 60min=14d. Por eso el histórico de 10 min se pide en ventanas
          de 2 días y se concatena.
    Header en todos: X-API-Key: {key}
    Nota: temperature viene en °F y humidity en % (lecturas crudas del sensor).

Requiere en el .env (raíz del repo):
    PURPLEAIR_API_KEY=...        # read key de https://develop.purpleair.com
"""

import argparse
import csv
import glob
import json
import os
import signal
import sys
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
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

# Histórico (endpoint /history).
HISTORY_FIELDS = "pm2.5_atm,temperature,humidity"  # time_stamp viene siempre incluido
HISTORY_AVERAGE = 10        # minutos; el mínimo del histórico (no hay 5 min)
HISTORY_BACKFILL_DAYS = 7   # rango por defecto si no hay datos locales
HISTORY_CHUNK_DAYS = 2      # ventana por request (límite de 3d para average=10)

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


# --------------------------- Histórico (backfill) ---------------------------

def _day_path(day_str):
    """Ruta del CSV de un día concreto ('YYYY-MM-DD')."""
    logs_dir = ROOT / "energy_logs" / "purpleair"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return str(logs_dir / f"{day_str}.csv")


def last_saved_epoch():
    """Epoch (UTC, s) del timestamp más reciente ya guardado, o None si no hay."""
    latest = None
    for fp in glob.glob(str(ROOT / "energy_logs" / "purpleair" / "*.csv")):
        try:
            with open(fp, newline="") as f:
                for r in csv.DictReader(f):
                    ts = r.get("timestamp")
                    if not ts:
                        continue
                    try:
                        ep = datetime.fromisoformat(ts).timestamp()
                    except ValueError:
                        continue
                    if latest is None or ep > latest:
                        latest = ep
        except OSError:
            continue
    return latest


def fetch_history(sensor_id, start_epoch, end_epoch, sensor_name):
    """Descarga el histórico de un sensor en ventanas, devuelve filas para el CSV.

    Concatena requests de HISTORY_CHUNK_DAYS días (el endpoint limita el rango por
    request según el promedio). Localiza cada campo por nombre en `fields` para no
    depender del orden de columnas.
    """
    rows = []
    chunk = HISTORY_CHUNK_DAYS * 86400
    s = int(start_epoch)
    end = int(end_epoch)
    while s < end:
        e = min(s + chunk, end)
        q = urllib.parse.urlencode({
            "start_timestamp": s,
            "end_timestamp": e,
            "average": HISTORY_AVERAGE,
            "fields": HISTORY_FIELDS,
        })
        url = f"{API_URL}/{sensor_id}/history?{q}"
        req = urllib.request.Request(url, headers={"X-API-Key": API_KEY})
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.load(resp)

        fields = payload.get("fields") or []
        idx = {name: i for i, name in enumerate(fields)}
        ts_i = idx.get("time_stamp")
        for d in payload.get("data") or []:
            if ts_i is None or ts_i >= len(d):
                continue
            local = datetime.fromtimestamp(d[ts_i], tz=timezone.utc).astimezone(TIMEZONE)
            get = lambda name: (d[idx[name]] if name in idx and idx[name] < len(d) else "")
            rows.append({
                "timestamp": local.isoformat(timespec="seconds"),
                "sensor_id": sensor_id,
                "sensor_name": sensor_name,
                "pm25": get("pm2.5_atm"),
                "temperature": get("temperature"),
                "humidity": get("humidity"),
            })
        s = e
    return rows


def merge_into_csvs(rows):
    """Mezcla filas (de varios días) en sus CSVs diarios, deduplicando por
    (sensor_id, timestamp) y dejando cada archivo ordenado por timestamp."""
    by_day = {}
    for r in rows:
        by_day.setdefault(r["timestamp"][:10], []).append(r)

    written = 0
    for day, new_rows in by_day.items():
        path = _day_path(day)
        existing = []
        if os.path.isfile(path):
            with open(path, newline="") as f:
                existing = list(csv.DictReader(f))
        merged = {}
        for r in existing + new_rows:  # las nuevas pisan a las viejas con misma clave
            merged[(str(r["sensor_id"]), r["timestamp"])] = r
        ordered = sorted(merged.values(), key=lambda r: (r["timestamp"], str(r["sensor_id"])))
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(ordered)
        written += len(new_rows)
    return written


def backfill():
    """Descarga el histórico de todos los sensores y lo mezcla en los CSVs diarios."""
    end_epoch = now_local().timestamp()
    last = last_saved_epoch()
    if last is None:
        start_epoch = end_epoch - HISTORY_BACKFILL_DAYS * 86400
        console.print(f"[cyan]Backfill[/cyan]: sin datos locales · descargando últimos {HISTORY_BACKFILL_DAYS} días")
    else:
        start_epoch = last + 1
        if start_epoch >= end_epoch:
            console.print("[cyan]Backfill[/cyan]: histórico al día (último dato es reciente)")
            return
        gap_h = (end_epoch - last) / 3600
        console.print(f"[cyan]Backfill[/cyan]: rellenando desde el último dato (~{gap_h:.1f} h atrás)")

    all_rows = []
    for sensor_id, ref in SENSORS.items():
        try:
            name = fetch_sensor(sensor_id).get("name") or ref
        except Exception:
            name = ref
        try:
            rows = fetch_history(sensor_id, start_epoch, end_epoch, name)
            all_rows.extend(rows)
            console.print(f"  {ref} ({sensor_id}): {len(rows)} puntos históricos")
        except urllib.error.HTTPError as e:
            console.print(f"  [red]HTTP {e.code} en histórico de {sensor_id} ({ref}):[/red] {e.reason}")
        except Exception as e:
            console.print(f"  [red]Error en histórico de {sensor_id} ({ref}):[/red] {e}")

    if all_rows:
        written = merge_into_csvs(all_rows)
        console.print(f"[green]Backfill listo[/green]: {written} filas escritas en los CSVs diarios")
    else:
        console.print("[yellow]Backfill sin filas nuevas[/yellow]")


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


def main(once=False, do_backfill=True, backfill_only=False):
    console.rule("[bold]PurpleAir Collector[/bold]")
    if not API_KEY:
        console.print("[red]Falta PURPLEAIR_API_KEY en el .env[/red]")
        sys.exit(1)

    # 1) Histórico: rellena los CSVs retroactivamente al arrancar.
    if do_backfill:
        try:
            backfill()
        except Exception as e:
            console.print(f"[red]Error en backfill:[/red] {e}")

    if backfill_only:
        return

    # 2) Lectura "una vez" (--once): completa los CSVs con el valor actual.
    if once:
        try:
            poll_once()
        except Exception as e:
            console.print(f"[red]Error en lectura:[/red] {e}")
            sys.exit(1)
        return

    console.print(f"\nMonitoreando {len(SENSORS)} sensores PurpleAir · intervalo {POLL_INTERVAL}s · Ctrl+C para detener\n")
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
    parser.add_argument("--once", action="store_true", help="Backfill + una sola lectura y salir")
    parser.add_argument("--no-backfill", action="store_true", help="Omite la descarga del histórico")
    parser.add_argument("--backfill-only", action="store_true", help="Solo descarga el histórico, sin lectura live")
    args = parser.parse_args()
    main(once=args.once, do_backfill=not args.no_backfill, backfill_only=args.backfill_only)
