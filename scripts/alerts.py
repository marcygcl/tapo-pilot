"""
alerts.py — Envía alertas via SMS y WhatsApp cuando un aula lleva
más de FLAG_THRESHOLD_DAYS días escolares con el filtro apagado.

Se corre desde collect.py --once al final de cada lectura, o manualmente:
    uv run python scripts/alerts.py

Variables de entorno necesarias (.env o GitHub Secrets):
    TWILIO_SID       — Account SID de Twilio
    TWILIO_TOKEN     — Auth Token de Twilio
    TWILIO_FROM_SMS  — Número Twilio para SMS (ej: +12015551234)
    TWILIO_FROM_WA   — Número Twilio WhatsApp (ej: whatsapp:+14155238886)
    ALERT_TO         — Tu número de celular (ej: +573001234567)
"""

import os
import sys
import csv
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv()

from utils import DEVICES, TIMEZONE, SCHOOL_START, SCHOOL_END, FLAG_THRESHOLD_DAYS

TWILIO_SID      = os.getenv("TWILIO_SID")
TWILIO_TOKEN    = os.getenv("TWILIO_TOKEN")
TWILIO_FROM_SMS = os.getenv("TWILIO_FROM_SMS")
TWILIO_FROM_WA  = os.getenv("TWILIO_FROM_WA", "whatsapp:+14155238886")
ALERT_TO        = os.getenv("ALERT_TO")
LOGS_DIR        = Path(__file__).parent.parent / "energy_logs"

ALERT_LEVELS = {
    1: "⚠️ Alerta",    # 1 día apagado
    2: "🚨 Bandera",   # 2+ días apagado — visita requerida
}


def get_week_dates():
    today = datetime.now(tz=TIMEZONE)
    monday = today - timedelta(days=today.weekday())
    return [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(5)]


def is_school_hour(ts: str) -> bool:
    try:
        d = datetime.fromisoformat(ts)
        h = d.hour
        dow = d.weekday()
        sh, sm = map(int, SCHOOL_START.split(":"))
        eh, em = map(int, SCHOOL_END.split(":"))
        from datetime import time
        return dow < 5 and time(sh, sm) <= d.time() <= time(eh, em)
    except Exception:
        return False


def load_week_data(dates: list[str]) -> list[dict]:
    rows = []
    for date in dates:
        path = LOGS_DIR / f"{date}.csv"
        if not path.exists():
            continue
        with open(path) as f:
            for row in csv.DictReader(f):
                if row.get("timestamp"):
                    rows.append(row)
    return rows


def compute_flags(rows: list[dict]) -> dict[str, int]:
    """Retorna {alias: dias_apagados} para la semana actual."""
    school_rows = [r for r in rows if is_school_hour(r["timestamp"])]
    flags = {}
    for alias in DEVICES:
        a_rows  = [r for r in school_rows if r["alias"] == alias]
        days    = set(r["timestamp"][:10] for r in a_rows)
        days_on = set(r["timestamp"][:10] for r in a_rows if str(r.get("is_on","0")) == "1")
        days_off = len(days - days_on)
        if days and days_off > 0:
            flags[alias] = days_off
    return flags


def build_message(flags: dict[str, int]) -> str:
    now = datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d %H:%M")
    lines = [f"📡 Smart Plug Pilot — {now} (Bogotá)\n"]

    critical = {a: v for a, v in flags.items() if v >= 2}
    warning  = {a: v for a, v in flags.items() if v == 1}

    if critical:
        lines.append("🚨 VISITA REQUERIDA:")
        for alias, days in sorted(critical.items(), key=lambda x: -x[1]):
            cfg = DEVICES[alias]
            lines.append(f"  • {cfg['aula']} ({cfg['colegio']}) — {days} días apagado")

    if warning:
        lines.append("\n⚠️ MONITOREAR:")
        for alias, days in warning.items():
            cfg = DEVICES[alias]
            lines.append(f"  • {cfg['aula']} ({cfg['colegio']}) — {days} día apagado")

    lines.append(f"\nTotal: {len(critical)} críticas · {len(warning)} advertencias")
    return "\n".join(lines)


def send_sms(message: str) -> bool:
    try:
        from twilio.rest import Client
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        msg = client.messages.create(
            body=message,
            from_=TWILIO_FROM_SMS,
            to=ALERT_TO
        )
        print(f"  SMS enviado: {msg.sid}")
        return True
    except Exception as e:
        print(f"  Error enviando SMS: {e}")
        return False


def send_whatsapp(message: str) -> bool:
    try:
        from twilio.rest import Client
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        to_wa = f"whatsapp:{ALERT_TO}" if not ALERT_TO.startswith("whatsapp:") else ALERT_TO
        msg = client.messages.create(
            body=message,
            from_=TWILIO_FROM_WA,
            to=to_wa
        )
        print(f"  WhatsApp enviado: {msg.sid}")
        return True
    except Exception as e:
        print(f"  Error enviando WhatsApp: {e}")
        return False


def main():
    if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM_SMS, ALERT_TO]):
        print("Faltan variables de entorno de Twilio — revisar .env o GitHub Secrets")
        sys.exit(1)

    week = get_week_dates()
    rows = load_week_data(week)

    if not rows:
        print("Sin datos esta semana — no se envían alertas")
        return

    flags = compute_flags(rows)
    flags = {a: v for a, v in flags.items() if v >= FLAG_THRESHOLD_DAYS}

    if not flags:
        print("Sin banderas activas — no se envían alertas")
        return

    print(f"Banderas detectadas: {len(flags)}")
    for alias, days in flags.items():
        cfg = DEVICES[alias]
        print(f"  {cfg['aula']} ({cfg['colegio']}) — {days} días apagado")

    message = build_message(flags)
    print(f"\nMensaje:\n{message}\n")

    print("Enviando SMS...")
    send_sms(message)

    print("Enviando WhatsApp...")
    send_whatsapp(message)


if __name__ == "__main__":
    main()
