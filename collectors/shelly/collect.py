"""Collector Shelly (Shelly Cloud) — vía el endpoint /device/all_status.

Lee el estado on/off y los watts en tiempo real de todos los dispositivos de la
cuenta Shelly y los guarda en energy_logs/shelly/YYYY-MM-DD.csv con el MISMO
esquema base que el collector de Emporia, para que el dashboard los lea igual.

Uso:
    uv run python collectors/shelly/collect.py          # loop cada POLL_INTERVAL
    uv run python collectors/shelly/collect.py --once    # una lectura (GitHub Actions)

Requiere en el .env:
    SHELLY_SERVER=https://shelly-XX-eu.shelly.cloud
    SHELLY_AUTH_KEY=...

API (Shelly Cloud Control):
    POST https://{server}/device/all_status   -> estado de todos los dispositivos
    POST https://{server}/interface/device/list -> nombres asignados
    Headers: Content-Type: application/x-www-form-urlencoded
    Body:    auth_key={key}
"""

import argparse
import csv
import json
import os
import signal
import sys
import time as _time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # collectors/ -> common
from rich.console import Console
from rich.table import Table

from common import (
    POLL_INTERVAL,
    SCHOOLS_BY_ID,
    SHELLY_AUTH_KEY,
    SHELLY_DEVICES,
    SHELLY_SERVER,
    is_school_hours,
    log_path,
    now_bogota,
)

COLLECTOR = "shelly"
console = Console()
running = True
CSV_FIELDS = ["timestamp", "alias", "aula", "colegio", "is_on", "watts", "today_wh", "school_hours"]

# Línea base de energía acumulada por día para derivar today_wh (Shelly solo
# expone el contador total acumulado, no el consumo del día).
BASELINE_FILE = "_energy_baseline.json"


def handle_exit(sig, frame):
    global running
    console.print("\n[yellow]Deteniendo...[/yellow]")
    running = False


signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)


def _base_url():
    s = SHELLY_SERVER or ""
    return s if s.startswith("http") else f"https://{s}"


def _post(path):
    data = urllib.parse.urlencode({"auth_key": SHELLY_AUTH_KEY}).encode()
    req = urllib.request.Request(
        _base_url().rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_names():
    """Mapa {id: nombre} desde /interface/device/list (best-effort)."""
    try:
        payload = _post("/interface/device/list")
        devices = (payload.get("data") or {}).get("devices") or {}
        return {did: (d.get("name") or "").strip() for did, d in devices.items()}
    except Exception:
        return {}


def extract(status):
    """(is_on, watts, energy_wh_total) de un status, soportando Gen1 y Gen2/3."""
    is_on, watts, energy = None, None, None
    for k, v in status.items():
        if k.startswith("switch:") and isinstance(v, dict):  # Gen2/3
            is_on = v.get("output")
            watts = v.get("apower")
            ae = v.get("aenergy") or {}
            if ae.get("total") is not None:
                energy = float(ae["total"])  # Wh acumulados
            break
    if is_on is None and isinstance(status.get("relays"), list) and status["relays"]:  # Gen1
        is_on = status["relays"][0].get("ison")
    if isinstance(status.get("meters"), list) and status["meters"]:
        m = status["meters"][0]
        if watts is None:
            watts = m.get("power")
        if energy is None and m.get("total") is not None:
            energy = float(m["total"]) / 60.0  # Gen1: Watt-min -> Wh
    return is_on, watts, energy


def _baseline_path():
    return Path(log_path(COLLECTOR)).parent / BASELINE_FILE


def _load_baseline():
    try:
        return json.loads(_baseline_path().read_text())
    except Exception:
        return {}


def _save_baseline(b):
    try:
        _baseline_path().write_text(json.dumps(b))
    except Exception as e:
        console.print(f"  [yellow]aviso: no se pudo guardar baseline de energía: {e}[/yellow]")


def read_all():
    """Una lectura de todos los dispositivos Shelly -> filas para el CSV."""
    payload = _post("/device/all_status")
    if not payload.get("isok"):
        raise RuntimeError(f"respuesta no OK: {json.dumps(payload)[:300]}")
    devices = (payload.get("data") or {}).get("devices_status") or {}
    names = fetch_names()

    today = now_bogota().strftime("%Y-%m-%d")
    baseline = _load_baseline()
    if baseline.get("date") != today:
        baseline = {"date": today, "totals": {}}

    ts = now_bogota().isoformat(timespec="seconds")
    school = int(is_school_hours())
    rows = []
    for dev_id, status in devices.items():
        cfg = SHELLY_DEVICES.get(dev_id, {})
        alias = cfg.get("alias") or names.get(dev_id) or dev_id
        aula = cfg.get("aula", "")
        colegio = SCHOOLS_BY_ID.get(cfg.get("school_id"), "")

        is_on, watts, energy = extract(status)

        if energy is not None:
            base = baseline["totals"].setdefault(dev_id, energy)
            today_wh = round(max(0.0, energy - base), 1)
        else:
            today_wh = 0.0

        rows.append({
            "timestamp": ts,
            "alias": alias,
            "aula": aula,
            "colegio": colegio,
            "is_on": int(bool(is_on)) if is_on is not None else "",
            "watts": round(float(watts), 1) if isinstance(watts, (int, float)) else 0.0,
            "today_wh": today_wh,
            "school_hours": school,
        })

    _save_baseline(baseline)
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
    t = Table(title=f"Shelly — {now_bogota().strftime('%Y-%m-%d %H:%M:%S')} (Bogotá)")
    t.add_column("Alias", style="bold")
    t.add_column("Aula")
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
        t.add_row(r["alias"], r["aula"] or "—", estado, str(r["watts"]), str(r["today_wh"]))
    return t


def poll_once():
    rows = read_all()
    append_csv(rows)
    console.print(make_table(rows))
    return rows


def main(once=False):
    console.rule("[bold]Shelly Collector[/bold]")
    if not SHELLY_SERVER or not SHELLY_AUTH_KEY:
        console.print("[red]Faltan credenciales SHELLY_SERVER / SHELLY_AUTH_KEY[/red]")
        sys.exit(1)

    if once:
        try:
            poll_once()
        except Exception as e:
            console.print(f"[red]Error en lectura:[/red] {e}")
            sys.exit(1)
        return

    console.print(f"Monitoreando dispositivos Shelly · intervalo {POLL_INTERVAL}s · Ctrl+C para detener\n")
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
    parser = argparse.ArgumentParser(description="Collector Shelly")
    parser.add_argument("--once", action="store_true", help="Una sola lectura y salir (CI)")
    args = parser.parse_args()
    main(once=args.once)
