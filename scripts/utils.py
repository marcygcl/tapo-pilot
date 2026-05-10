"""
utils.py — Funciones compartidas y configuración de dispositivos del piloto.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuración global
# ---------------------------------------------------------------------------

TAPO_EMAIL    = os.getenv("TAPO_EMAIL")
TAPO_PASSWORD = os.getenv("TAPO_PASSWORD")
TIMEZONE      = ZoneInfo(os.getenv("TIMEZONE", "America/Bogota"))
SCHOOL_START  = os.getenv("SCHOOL_START", "07:00")
SCHOOL_END    = os.getenv("SCHOOL_END",   "16:00")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "300"))
FLAG_THRESHOLD_DAYS = int(os.getenv("FLAG_THRESHOLD_DAYS", "1"))

# ---------------------------------------------------------------------------
# Registro de dispositivos
# Completar después de correr setup_plugs.py
# ---------------------------------------------------------------------------
# Formato: { "alias": { "ip": str, "aula": str, "colegio": str } }

DEVICES: dict[str, dict] = {
    # Ejemplos — reemplazar con datos reales tras setup_plugs.py
    # "plug_p1": {"ip": "192.168.1.101", "aula": "Aula 101", "colegio": "Col. San Francisco"},
}

# ---------------------------------------------------------------------------
# Wattage del Sqair por intensidad
# ---------------------------------------------------------------------------
SQAIR_INTENSITY = {
    "off":    (0,   2),    # apagado: 0–2W
    "low":    (3,   10),   # baja:    3–10W
    "medium": (11,  28),   # media:  11–28W
    "high":   (29,  42),   # alta:   29–42W
}

def classify_intensity(watts: float) -> str:
    """Clasifica el nivel de intensidad del filtro según consumo actual."""
    for level, (lo, hi) in SQAIR_INTENSITY.items():
        if lo <= watts <= hi:
            return level
    return "unknown"

def now_bogota() -> datetime:
    """Retorna la hora actual en zona horaria de Bogotá."""
    return datetime.now(tz=TIMEZONE)

def is_school_hours(dt: datetime | None = None) -> bool:
    """Retorna True si el momento dado está dentro del horario escolar (L–V)."""
    dt = dt or now_bogota()
    if dt.weekday() >= 5:   # sábado=5, domingo=6
        return False
    start_h, start_m = map(int, SCHOOL_START.split(":"))
    end_h,   end_m   = map(int, SCHOOL_END.split(":"))
    t = dt.time()
    from datetime import time
    return time(start_h, start_m) <= t <= time(end_h, end_m)

def log_path(date: datetime | None = None) -> str:
    """Retorna la ruta del CSV del día dado (o hoy si no se especifica)."""
    d = (date or now_bogota()).strftime("%Y-%m-%d")
    logs_dir = os.path.join(os.path.dirname(__file__), "..", "energy_logs")
    os.makedirs(logs_dir, exist_ok=True)
    return os.path.join(logs_dir, f"{d}.csv")
