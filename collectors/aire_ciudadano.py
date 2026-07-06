"""Collector Aire Ciudadano (calidad del aire / PM2.5) — vía la API pública de fixstations.

Independiente del resto de collectors: cada 30 minutos pollea la red Aire Ciudadano
y registra las estaciones del proyecto Universidad Rosario. La lista de estaciones a
seguir NO se descubre en cada ciclo: se lee de un export de Grafana (lista fija de
~393 sensores "PUR_IED"). Así se captura TODA estación definida en el proyecto,
incluso las que ahora mismo están offline (no aparecen en /fixstations); esas se
registran con pm25 vacío (null).

Guarda en energy_logs/aire_ciudadano/YYYY-MM-DD.csv (un archivo por día).

Campos del CSV:
    timestamp, station_id, station_name, pm25, temperature, humidity, latitude, longitude
    (para una estación offline, pm25/temperature/humidity/latitude/longitude quedan
     vacíos; station_id = station_name = el nombre definido en la lista.)

Lista de estaciones:
    data/Sensores proyecto Universidad Rosario-*.json  (export de dashboard Grafana)
    Los nombres se extraen de las transformaciones "filterFieldsByName" del panel:
    include > names. Se filtran los que contienen "PUR_IED".

Uso:
    uv run python collectors/aire_ciudadano.py               # backfill + loop continuo (30 min)
    uv run python collectors/aire_ciudadano.py --once         # backfill + una lectura y salir
    uv run python collectors/aire_ciudadano.py --no-backfill  # omite el intento de histórico
    uv run python collectors/aire_ciudadano.py --backfill-only  # solo histórico, sin lectura live

API (Aire Ciudadano) — https://api.aireciudadano.com/fixstations
    GET /fixstations  -> lista de estaciones ONLINE, cada una con:
        id, station_name, decimalLatitude, decimalLongitude, observedOn,
        measurements: [{measurementType, measurementUnit, measurementValue, ...}, ...]
        measurementType ∈ {PM2.5, Temperature, Humidity, CO2}
    No requiere API key. PM2.5 en µg/m³, temperature en °C, humidity en %.
    Solo devuelve las estaciones que reportaron recientemente; las offline no salen.

    NOTA sobre el histórico: a diferencia de PurpleAir, la API pública de Aire
    Ciudadano SOLO expone la última lectura de cada estación (/fixstations es un
    snapshot). No hay endpoint de rango/histórico (probados /history, /historical,
    /sensordata, /fixstations/history → 404). Por eso el modo backfill se mantiene
    por paridad de interfaz con purpleair.py, pero no puede rellenar nada: avisa y
    sale. El histórico se construye acumulando las lecturas en vivo cada 30 min.
"""

import argparse
import csv
import glob
import os
import json
import signal
import sys
import time as _time
import urllib.error
import urllib.request
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

console = Console()
running = True

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "America/Bogota"))
POLL_INTERVAL = 1800  # 30 minutos, fijo para este collector

API_URL = "https://api.aireciudadano.com/fixstations"

# Filtro por nombre: el proyecto Rosario usa el prefijo "PUR_IED".
NAME_FILTER = "PUR_IED"

# Export de Grafana con la lista fija de estaciones del proyecto. Se toma el más
# reciente que matchee el patrón (el nombre lleva un timestamp al final).
SENSOR_LIST_GLOB = str(ROOT / "data" / "Sensores proyecto Universidad Rosario-*.json")

CSV_FIELDS = [
    "timestamp", "station_id", "station_name",
    "pm25", "temperature", "humidity", "latitude", "longitude",
]


def handle_exit(sig, frame):
    global running
    console.print("\n[yellow]Deteniendo...[/yellow]")
    running = False


signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)


def now_local():
    return datetime.now(tz=TIMEZONE)


def log_path(date=None):
    """Ruta del CSV diario: energy_logs/aire_ciudadano/YYYY-MM-DD.csv."""
    d = (date or now_local()).strftime("%Y-%m-%d")
    logs_dir = ROOT / "energy_logs" / "aire_ciudadano"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return str(logs_dir / f"{d}.csv")


@lru_cache(maxsize=1)
def load_sensor_list():
    """Lee la lista fija de estaciones PUR_IED del export de Grafana.

    Los nombres viven en las transformaciones 'filterFieldsByName' de los paneles,
    bajo options>include>names. Se recorre el JSON entero buscando esas listas, se
    hace la unión y se filtran los que contienen 'PUR_IED' (descarta 'Time', etc.).
    Devuelve una lista ordenada de nombres. Lanza si no encuentra el archivo.
    """
    matches = sorted(glob.glob(SENSOR_LIST_GLOB))
    if not matches:
        raise FileNotFoundError(f"No se encontró la lista de sensores: {SENSOR_LIST_GLOB}")
    path = matches[-1]  # el más reciente por nombre (lleva timestamp al final)
    with open(path) as f:
        doc = json.load(f)

    names = set()

    def walk(o):
        if isinstance(o, dict):
            inc = o.get("include")
            if isinstance(inc, dict) and "names" in inc:
                for n in inc["names"] or []:
                    names.add(n)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(doc)
    return sorted(n for n in names if NAME_FILTER in n)


def fetch_stations():
    """Descarga las estaciones ONLINE de la API. Devuelve dict {id: station}."""
    req = urllib.request.Request(API_URL, headers={"User-Agent": "tapo-pilot/aire_ciudadano"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.load(resp)
    stations = payload if isinstance(payload, list) else []
    return {s.get("id"): s for s in stations if s.get("id")}


def measurement_value(station, mtype):
    """Valor de la medición `mtype` (p.ej. 'PM2.5') de una estación, o '' si no está."""
    for m in station.get("measurements") or []:
        if m.get("measurementType") == mtype:
            return m.get("measurementValue", "")
    return ""


def read_all():
    """Una lectura de TODAS las estaciones definidas en la lista -> filas para el CSV.

    Las offline (no presentes en /fixstations) se registran con pm25/temperature/
    humidity/latitude/longitude vacíos.
    """
    ts = now_local().isoformat(timespec="seconds")
    online = fetch_stations()
    rows = []
    for name in load_sensor_list():
        s = online.get(name)
        if s is None:  # estación offline: solo dejamos constancia del nombre
            rows.append({
                "timestamp": ts,
                "station_id": name,
                "station_name": name,
                "pm25": "",
                "temperature": "",
                "humidity": "",
                "latitude": "",
                "longitude": "",
            })
            continue
        rows.append({
            "timestamp": ts,
            "station_id": s.get("id", name),
            "station_name": s.get("station_name") or name,
            "pm25": measurement_value(s, "PM2.5"),
            "temperature": measurement_value(s, "Temperature"),
            "humidity": measurement_value(s, "Humidity"),
            "latitude": s.get("decimalLatitude", ""),
            "longitude": s.get("decimalLongitude", ""),
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

def backfill():
    """Intento de histórico. La API de Aire Ciudadano no expone rango histórico
    (solo /fixstations, que es un snapshot de la última lectura), así que aquí no
    hay nada que rellenar. Se mantiene por paridad con purpleair.py y para que el
    aviso quede explícito en los logs."""
    console.print(
        "[cyan]Backfill[/cyan]: la API de Aire Ciudadano solo expone la última "
        "lectura (sin endpoint de histórico); nada que rellenar. El histórico se "
        "acumula con las lecturas en vivo."
    )


def make_table(online_rows):
    t = Table(title=f"Aire Ciudadano (PUR_IED) — {now_local().strftime('%Y-%m-%d %H:%M:%S')} (Bogotá)")
    t.add_column("Estación", style="bold")
    t.add_column("PM2.5", justify="right")
    t.add_column("Temp °C", justify="right")
    t.add_column("Humedad %", justify="right")
    for r in online_rows:
        t.add_row(
            str(r["station_name"]),
            str(r["pm25"]),
            str(r["temperature"]),
            str(r["humidity"]),
        )
    return t


def poll_once():
    rows = read_all()
    if not rows:
        console.print("  [yellow]Lista de sensores vacía; nada que registrar[/yellow]")
        return rows
    append_csv(rows)
    online = [r for r in rows if r["pm25"] != ""]
    offline = len(rows) - len(online)
    if online:
        console.print(make_table(online))
    console.print(
        f"  [green]{len(online)} online[/green] · "
        f"[dim]{offline} offline[/dim] · {len(rows)} definidas · guardadas en {log_path()}"
    )
    return rows


def main(once=False, do_backfill=True, backfill_only=False):
    console.rule("[bold]Aire Ciudadano Collector[/bold]")

    # 1) Histórico: la API no lo soporta, pero mantenemos la interfaz.
    if do_backfill:
        try:
            backfill()
        except Exception as e:
            console.print(f"[red]Error en backfill:[/red] {e}")

    if backfill_only:
        return

    # 2) Lectura "una vez" (--once).
    if once:
        try:
            poll_once()
        except Exception as e:
            console.print(f"[red]Error en lectura:[/red] {e}")
            sys.exit(1)
        return

    try:
        n_sensors = len(load_sensor_list())
    except Exception as e:
        console.print(f"[red]No se pudo cargar la lista de sensores:[/red] {e}")
        sys.exit(1)
    console.print(f"\nMonitoreando {n_sensors} estaciones '{NAME_FILTER}' (lista fija) · intervalo {POLL_INTERVAL}s · Ctrl+C para detener\n")
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
    parser = argparse.ArgumentParser(description="Collector Aire Ciudadano (PM2.5)")
    parser.add_argument("--once", action="store_true", help="Backfill + una sola lectura y salir")
    parser.add_argument("--no-backfill", action="store_true", help="Omite el intento de histórico")
    parser.add_argument("--backfill-only", action="store_true", help="Solo intenta el histórico, sin lectura live")
    args = parser.parse_args()
    main(once=args.once, do_backfill=not args.no_backfill, backfill_only=args.backfill_only)
