import os, json
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

CONFIG_PATH = Path(__file__).parent.parent / "config.json"
with open(CONFIG_PATH) as f:
    CONFIG = json.load(f)

PILOT         = CONFIG["pilot"]
TAPO_EMAIL    = os.getenv("TAPO_EMAIL")
TAPO_PASSWORD = os.getenv("TAPO_PASSWORD")
TIMEZONE      = ZoneInfo(os.getenv("TIMEZONE", PILOT["timezone"]))
SCHOOL_START  = os.getenv("SCHOOL_START",  PILOT["school_start"])
SCHOOL_END    = os.getenv("SCHOOL_END",    PILOT["school_end"])
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", PILOT["poll_interval_seconds"]))
FLAG_THRESHOLD_DAYS = int(os.getenv("FLAG_THRESHOLD_DAYS", PILOT["flag_threshold_days"]))

DEVICES: dict[str, dict] = {}
for school in CONFIG["schools"]:
    for plug in school["plugs"]:
        DEVICES[plug["tapo_alias"]] = {
            "aula":    plug["aula"],
            "colegio": school["name"],
            "ip":      plug["ip"],
        }

SQAIR_INTENSITY = {"off":(0,2),"low":(3,10),"medium":(11,28),"high":(29,42)}

def classify_intensity(watts):
    for level,(lo,hi) in SQAIR_INTENSITY.items():
        if lo<=watts<=hi: return level
    return "unknown"

def now_bogota():
    return datetime.now(tz=TIMEZONE)

def is_school_hours(dt=None):
    dt = dt or now_bogota()
    if dt.weekday()>=5: return False
    sh,sm = map(int,SCHOOL_START.split(":"))
    eh,em = map(int,SCHOOL_END.split(":"))
    from datetime import time
    return time(sh,sm)<=dt.time()<=time(eh,em)

def log_path(date=None):
    d=(date or now_bogota()).strftime("%Y-%m-%d")
    logs_dir=Path(__file__).parent.parent/"energy_logs"
    logs_dir.mkdir(exist_ok=True)
    return str(logs_dir/f"{d}.csv")
