import os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TAPO_EMAIL    = os.getenv("TAPO_EMAIL")
TAPO_PASSWORD = os.getenv("TAPO_PASSWORD")
TIMEZONE      = ZoneInfo(os.getenv("TIMEZONE", "America/Bogota"))
SCHOOL_START  = os.getenv("SCHOOL_START", "07:00")
SCHOOL_END    = os.getenv("SCHOOL_END",   "16:00")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "300"))
FLAG_THRESHOLD_DAYS = int(os.getenv("FLAG_THRESHOLD_DAYS", "1"))

DEVICES: dict[str, dict] = {
    "lamp":   {"aula": "Aula 101", "colegio": "Colombia School 1"},
    "tv":     {"aula": "Aula 102", "colegio": "Colombia School 2"},
    "air":    {"aula": "Aula 103", "colegio": "Colombia School 3"},
    "coffee": {"aula": "Aula 104", "colegio": "Colombia School 4"},
    "eco":    {"aula": "Aula 201", "colegio": "Colombia School 5"},
    "fridge": {"aula": "Aula 202", "colegio": "Colombia School 6"},
}

SQAIR_INTENSITY = {
    "off":    (0,   2),
    "low":    (3,   10),
    "medium": (11,  28),
    "high":   (29,  42),
}

def classify_intensity(watts: float) -> str:
    for level, (lo, hi) in SQAIR_INTENSITY.items():
        if lo <= watts <= hi:
            return level
    return "unknown"

def now_bogota() -> datetime:
    return datetime.now(tz=TIMEZONE)

def is_school_hours(dt=None) -> bool:
    dt = dt or now_bogota()
    if dt.weekday() >= 5:
        return False
    start_h, start_m = map(int, SCHOOL_START.split(":"))
    end_h,   end_m   = map(int, SCHOOL_END.split(":"))
    from datetime import time
    return time(start_h, start_m) <= dt.time() <= time(end_h, end_m)

def log_path(date=None) -> str:
    d = (date or now_bogota()).strftime("%Y-%m-%d")
    logs_dir = os.path.join(os.path.dirname(__file__), "..", "energy_logs")
    os.makedirs(logs_dir, exist_ok=True)
    return os.path.join(logs_dir, f"{d}.csv")
