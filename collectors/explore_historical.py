"""Exploración de histórico disponible por API: Emporia vs Shelly.

EMPORIA: usa pyemvue.get_chart_usage() sobre el canal Main del device `one`
(gid 606961) para descubrir qué rango de fechas y granularidad expone la nube.
SHELLY: revisa si /device/all_status trae histórico (spoiler: solo el bloque
aenergy con los últimos 3 minutos por minuto + el contador acumulado).

Guarda muestras en:
  energy_logs/emporia_historical_sample.json
  energy_logs/shelly_historical_sample.json

Imprime: rango disponible por tech, total de lecturas históricas y campos.

Uso:
    uv run python collectors/explore_historical.py
"""

import datetime
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # collectors/ -> common
from rich.console import Console
from rich.table import Table

from common import (
    EMPORIA_EMAIL,
    EMPORIA_PASSWORD,
    ROOT,
    SHELLY_AUTH_KEY,
    SHELLY_DEVICES,
    SHELLY_SERVER,
)

console = Console()
TOKEN_FILE = str(Path(__file__).resolve().parent / "emporia" / ".emporia_tokens.json")
EMP_GID = 606961  # device 'one'
OUT_EMP = ROOT / "energy_logs" / "emporia_historical_sample.json"
OUT_SHE = ROOT / "energy_logs" / "shelly_historical_sample.json"


# ------------------------------ Emporia ------------------------------
def explore_emporia():
    from pyemvue import PyEmVue
    from pyemvue.enums import Scale, Unit

    vue = PyEmVue()
    vue.login(username=EMPORIA_EMAIL, password=EMPORIA_PASSWORD, token_storage_file=TOKEN_FILE)
    devices = vue.get_devices()
    dev = next((d for d in devices if d.device_gid == EMP_GID), None)
    if dev is None:
        raise RuntimeError(f"device gid {EMP_GID} no encontrado en la cuenta")
    channel = dev.channels[0]  # canal Main "1,2,3"
    now = datetime.datetime.now(datetime.timezone.utc)

    # Probar varios rangos a escala DÍA para descubrir profundidad del histórico.
    probes = []
    for days in (7, 30, 90, 365, 730):
        start = now - datetime.timedelta(days=days)
        try:
            usage, start_time = vue.get_chart_usage(
                channel, start, now, scale=Scale.DAY.value, unit=Unit.KWH.value)
            non_null = [u for u in usage if u is not None]
            probes.append({
                "requested_days": days,
                "buckets_returned": len(usage),
                "non_null": len(non_null),
                "sum_kwh": round(sum(non_null), 3) if non_null else 0,
                "start_time": start_time.isoformat() if start_time else None,
            })
        except Exception as e:
            probes.append({"requested_days": days, "error": str(e)[:80]})

    # Muestra detallada: serie diaria de los últimos 30 días con timestamp por bucket.
    start = now - datetime.timedelta(days=30)
    usage, start_time = vue.get_chart_usage(
        channel, start, now, scale=Scale.DAY.value, unit=Unit.KWH.value)
    series = []
    if start_time:
        for i, u in enumerate(usage):
            ts = start_time + datetime.timedelta(days=i)
            series.append({"timestamp": ts.isoformat(), "usage_kwh": u})

    # Probar granularidad fina (HORA) en las últimas 24h para documentar resolución.
    hstart = now - datetime.timedelta(hours=24)
    try:
        husage, hstart_time = vue.get_chart_usage(
            channel, hstart, now, scale=Scale.HOUR.value, unit=Unit.KWH.value)
        hourly = {"buckets": len(husage), "non_null": len([u for u in husage if u is not None]),
                  "scale": "1H", "first_start": hstart_time.isoformat() if hstart_time else None}
    except Exception as e:
        hourly = {"error": str(e)[:80]}

    non_null_series = [s for s in series if s["usage_kwh"] is not None]
    sample = {
        "_source": "pyemvue.get_chart_usage(channel Main, scale=1D, unit=kWh)",
        "device_gid": EMP_GID,
        "channel": channel.channel_num,
        "queried_end_utc": now.isoformat(),
        "depth_probes_by_day_scale": probes,
        "hourly_resolution_last_24h": hourly,
        "fields": ["timestamp", "usage_kwh"],
        "sample_scale": "1D (día)",
        "buckets_30d": len(series),
        "non_null_30d": len(non_null_series),
        "data_30d": series,
    }
    OUT_EMP.write_text(json.dumps(sample, indent=2, ensure_ascii=False))
    return sample


# ------------------------------ Shelly -------------------------------
def _shelly_post(path):
    base = SHELLY_SERVER if (SHELLY_SERVER or "").startswith("http") else f"https://{SHELLY_SERVER}"
    data = urllib.parse.urlencode({"auth_key": SHELLY_AUTH_KEY}).encode()
    req = urllib.request.Request(base.rstrip("/") + path, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def explore_shelly():
    payload = _shelly_post("/device/all_status")
    if not payload.get("isok"):
        raise RuntimeError(f"respuesta no OK: {json.dumps(payload)[:120]}")
    ds = (payload.get("data") or {}).get("devices_status") or {}
    # Tomamos un device Shelly configurado.
    dev_id = next((i for i in ds if i in SHELLY_DEVICES), next(iter(ds), None))
    status = ds.get(dev_id, {}) if dev_id else {}

    # Lo único "histórico" en all_status es switch:N.aenergy (by_minute = últimos 3 min).
    aenergy = {}
    for k, v in status.items():
        if k.startswith("switch:") and isinstance(v, dict) and isinstance(v.get("aenergy"), dict):
            aenergy = v["aenergy"]
            break

    by_minute = aenergy.get("by_minute") or []
    minute_ts = aenergy.get("minute_ts")
    minute_series = []
    if isinstance(minute_ts, int):
        # by_minute[0] es el minuto actual; retrocedemos un minuto por bucket.
        for i, wmin in enumerate(by_minute):
            ts = datetime.datetime.fromtimestamp(minute_ts - i * 60, datetime.timezone.utc)
            minute_series.append({"timestamp": ts.isoformat(), "milliwatt_hours": wmin})

    sample = {
        "_source": "/device/all_status (Shelly Cloud) — NO hay endpoint de rango histórico aquí",
        "device_id": dev_id,
        "model_code": status.get("code"),
        "exposes_date_range_history": False,
        "history_available_in_all_status": {
            "aenergy.total_wh": aenergy.get("total"),     # contador acumulado (no se pierde)
            "aenergy.by_minute": by_minute,               # últimos 3 minutos (mWh por minuto)
            "aenergy.minute_ts": minute_ts,
        },
        "fields": ["timestamp", "milliwatt_hours (by_minute)", "total_wh (acumulado)"],
        "last_3min_series": minute_series,
        "note": ("all_status solo expone aenergy.by_minute (3 buckets de 1 min) y el contador "
                 "acumulado aenergy.total. Para histórico real con rango de fechas se necesita "
                 "otro endpoint del cloud Shelly (estadísticas/consumo), no /device/all_status."),
    }
    OUT_SHE.write_text(json.dumps(sample, indent=2, ensure_ascii=False))
    return sample


# ------------------------------ resumen ------------------------------
def main():
    console.rule("[bold]Exploración de histórico — Emporia vs Shelly[/bold]")
    OUT_EMP.parent.mkdir(parents=True, exist_ok=True)

    emp, she = None, None
    if EMPORIA_EMAIL and EMPORIA_PASSWORD:
        try:
            emp = explore_emporia()
        except Exception as e:
            console.print(f"[red]Error Emporia:[/red] {e}")
    else:
        console.print("[yellow]Faltan credenciales Emporia; se omite.[/yellow]")

    if SHELLY_SERVER and SHELLY_AUTH_KEY:
        try:
            she = explore_shelly()
        except Exception as e:
            console.print(f"[red]Error Shelly:[/red] {e}")
    else:
        console.print("[yellow]Faltan credenciales Shelly; se omite.[/yellow]")

    t = Table(show_header=True, header_style="bold")
    t.add_column("Aspecto")
    t.add_column("Emporia")
    t.add_column("Shelly")

    # Rango disponible
    emp_range = "—"
    if emp:
        deep = [p for p in emp["depth_probes_by_day_scale"] if p.get("non_null")]
        max_days = max((p["requested_days"] for p in deep), default=0)
        emp_range = f"≥ {max_days} días (escala día); resolución hasta horaria" if max_days else "sin datos"
    she_range = "—"
    if she:
        she_range = "solo últimos 3 min (by_minute) + total acumulado · sin rango histórico"
    t.add_row("Rango disponible", emp_range, she_range)

    # Total lecturas históricas
    emp_total = str(emp["non_null_30d"]) + " buckets/día (30d)" if emp else "—"
    she_total = str(len(she["last_3min_series"])) + " buckets/min (3m)" if she else "—"
    t.add_row("Total lecturas históricas", emp_total, she_total)

    # Campos
    t.add_row("Campos en el histórico",
              ", ".join(emp["fields"]) if emp else "—",
              ", ".join(she["fields"]) if she else "—")
    t.add_row("Endpoint / método",
              "pyemvue.get_chart_usage()" if emp else "—",
              "/device/all_status (sin histórico real)" if she else "—")
    console.print(t)

    if emp:
        console.print("\n[bold]Emporia — sondeos de profundidad (escala día):[/bold]")
        for p in emp["depth_probes_by_day_scale"]:
            if "error" in p:
                console.print(f"  {p['requested_days']:>4}d → error: {p['error']}")
            else:
                console.print(f"  {p['requested_days']:>4}d → {p['non_null']}/{p['buckets_returned']} "
                              f"buckets con datos · {p['sum_kwh']} kWh")
    if she:
        console.print(f"\n[bold]Shelly — aenergy:[/bold] total={she['history_available_in_all_status']['aenergy.total_wh']} Wh · "
                      f"by_minute(últimos 3 min, mWh)={she['history_available_in_all_status']['aenergy.by_minute']}")
        console.print(f"[dim]{she['note']}[/dim]")

    console.print(f"\n[dim]Muestras guardadas:[/dim]")
    if emp:
        console.print(f"  {OUT_EMP}")
    if she:
        console.print(f"  {OUT_SHE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
