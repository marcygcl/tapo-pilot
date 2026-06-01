"""Configuración y utilidades compartidas por todos los collectors.

Cada collector (tapo, emporia, shelly) vive en su propio subdirectorio y guarda
sus CSVs en energy_logs/<collector>/YYYY-MM-DD.csv. Este módulo centraliza la
carga del .env, la config, el manejo de horario escolar y la ruta de logs para
que los collectors no dupliquen esa lógica.
"""

import json
import os
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

with open(ROOT / "config.json") as f:
    CONFIG = json.load(f)

PILOT = CONFIG["pilot"]
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", PILOT["timezone"]))
SCHOOL_START = os.getenv("SCHOOL_START", PILOT["school_start"])
SCHOOL_END = os.getenv("SCHOOL_END", PILOT["school_end"])
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", PILOT["poll_interval_seconds"]))

# Credenciales por proveedor.
TAPO_EMAIL = os.getenv("TAPO_EMAIL")
TAPO_PASSWORD = os.getenv("TAPO_PASSWORD")
EMPORIA_EMAIL = os.getenv("EMPORIA_EMAIL")
EMPORIA_PASSWORD = os.getenv("EMPORIA_PASSWORD")

# Rangos de intensidad del filtro Sqair (watts) -> etiqueta cualitativa.
SQAIR_INTENSITY = {"off": (0, 2), "low": (3, 10), "medium": (11, 28), "high": (29, 42)}


def classify_intensity(watts):
    for level, (lo, hi) in SQAIR_INTENSITY.items():
        if lo <= watts <= hi:
            return level
    return "unknown"


def now_bogota():
    return datetime.now(tz=TIMEZONE)


def is_school_hours(dt=None):
    dt = dt or now_bogota()
    if dt.weekday() >= 5:  # sábado/domingo
        return False
    sh, sm = map(int, SCHOOL_START.split(":"))
    eh, em = map(int, SCHOOL_END.split(":"))
    return time(sh, sm) <= dt.time() <= time(eh, em)


def log_path(collector, date=None):
    """Ruta del CSV diario para un collector: energy_logs/<collector>/<fecha>.csv."""
    d = (date or now_bogota()).strftime("%Y-%m-%d")
    logs_dir = ROOT / "energy_logs" / collector
    logs_dir.mkdir(parents=True, exist_ok=True)
    return str(logs_dir / f"{d}.csv")


# Dispositivos Tapo declarados en config.json (indexados por alias).
TAPO_DEVICES = {}
for school in CONFIG["schools"]:
    for plug in school["plugs"]:
        TAPO_DEVICES[plug["tapo_alias"]] = {
            "aula": plug["aula"],
            "colegio": school["name"],
            "ip": plug["ip"],
        }

# Dispositivos Emporia declarados en config.json (indexados por gid).
EMPORIA_DEVICES = {}
for dev in CONFIG.get("emporia", {}).get("devices", []):
    EMPORIA_DEVICES[dev["gid"]] = dev
