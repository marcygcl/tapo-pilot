"""Benchmark CONTINUO Emporia vs Shelly.

Corre sin pausas (hasta Ctrl+C): lee ambas tecnologías cada `--interval` (default
30s) y escribe en UN CSV por día (energy_logs/benchmark/YYYY-MM-DD.csv, se va
actualizando durante el día). Mantiene manifest.json para que el dashboard lo
enumere. Captura todos los campos útiles de cada API, no solo watts.

Nunca aborta: si una lectura falla/timeout, registra error_msg + http_status y
sigue. Cada 20 lecturas imprime un estado breve. Ctrl+C cierra con un resumen.

Esquema CSV:
  timestamp, device, technology, alias, is_on, watts, today_wh, latency_ms,
  error_msg, http_status, voltage, current, freq, temperature, wifi_rssi, aenergy_total

Campos extra por tecnología:
  Shelly: voltage, current, freq, temperature(°C), wifi_rssi, aenergy_total(Wh)
  Emporia: la API de uso no expone voltaje/corriente/frecuencia/temperatura;
           esos campos quedan vacíos (no hay equivalente disponible).

Uso:
    nohup uv run python collectors/benchmark.py > benchmark.log 2>&1 &   # lanzar
    pkill -f "python collectors/benchmark.py"                            # detener
    uv run python collectors/benchmark.py --interval 30
    uv run python collectors/benchmark.py --quick --max-polls 3          # prueba
"""

import argparse
import csv
import datetime
import json
import signal
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # collectors/ -> common
from rich.console import Console
from rich.table import Table

from common import (
    EMPORIA_DEVICES,
    EMPORIA_EMAIL,
    EMPORIA_PASSWORD,
    ROOT,
    SHELLY_AUTH_KEY,
    SHELLY_DEVICES,
    SHELLY_SERVER,
    now_bogota,
)

console = Console()
MAIN_CHANNEL = "1,2,3"
TOKEN_FILE = str(Path(__file__).resolve().parent / "emporia" / ".emporia_tokens.json")
EXTRA_FIELDS = ["voltage", "current", "freq", "temperature", "wifi_rssi", "aenergy_total"]
CSV_FIELDS = (["timestamp", "device", "technology", "alias", "is_on", "watts", "today_wh",
               "latency_ms", "error_msg", "http_status"] + EXTRA_FIELDS)

running = True


# ----------------------------- utilidades -----------------------------
def safe_stdev(xs):
    return statistics.pstdev(xs) if len(xs) >= 1 else None


def _n(x):
    return round(float(x), 2) if isinstance(x, (int, float)) else ""


def http_code(e):
    if isinstance(e, urllib.error.HTTPError):
        return e.code
    resp = getattr(e, "response", None)
    if resp is not None and getattr(resp, "status_code", None):
        return resp.status_code
    return ""


def blank_extra():
    return {f: "" for f in EXTRA_FIELDS}


def _handle_sigint(sig, frame):
    global running
    if running:
        console.print("\n[yellow]Ctrl+C — cerrando y generando resumen...[/yellow]")
    running = False


def sleep_interruptible(secs):
    end = time.monotonic() + secs
    while running and time.monotonic() < end:
        time.sleep(min(1.0, max(0.0, end - time.monotonic())))


# ----------------------------- Emporia --------------------------------
def emporia_connect():
    from pyemvue import PyEmVue
    vue = PyEmVue()
    vue.login(username=EMPORIA_EMAIL, password=EMPORIA_PASSWORD, token_storage_file=TOKEN_FILE)
    return vue


def _emp_channel(usage_device):
    if not usage_device or not usage_device.channels:
        return None
    return usage_device.channels.get(MAIN_CHANNEL) or next(iter(usage_device.channels.values()), None)


def poll_emporia(vue):
    """Devuelve (rows, latency_ms, ok). Una fila por device; nunca lanza."""
    from pyemvue.enums import Scale, Unit
    retry = dict(max_retry_attempts=2, initial_retry_delay=0.5, max_retry_delay=2.0)
    t0 = time.monotonic()
    try:
        devices = vue.get_devices()
        gids = [d.device_gid for d in devices]
        now = datetime.datetime.now(datetime.timezone.utc)
        watt = vue.get_device_list_usage(gids, now, scale=Scale.SECOND.value, unit=Unit.KWH.value, **retry)
        day = vue.get_device_list_usage(gids, now, scale=Scale.DAY.value, unit=Unit.KWH.value, **retry)
        outlets = {o.device_gid: o.outlet_on for o in vue.get_outlets()}
        lat = (time.monotonic() - t0) * 1000
        rows = []
        for d in devices:
            cfg = EMPORIA_DEVICES.get(d.device_gid)
            if not cfg:
                continue
            ch_w, ch_d = _emp_channel(watt.get(d.device_gid)), _emp_channel(day.get(d.device_gid))
            watts = round((ch_w.usage or 0.0) * 3600 * 1000, 2) if ch_w else 0.0
            today_wh = round((ch_d.usage or 0.0) * 1000, 1) if ch_d else 0.0
            is_on = outlets.get(d.device_gid)
            if is_on is None and d.outlet is not None:
                is_on = d.outlet.outlet_on
            rows.append({
                "device": str(d.device_gid), "technology": "emporia", "alias": cfg["alias"],
                "is_on": int(bool(is_on)) if is_on is not None else "",
                "watts": watts, "today_wh": today_wh, "latency_ms": round(lat, 1),
                "error_msg": "", "http_status": 200, **blank_extra(),
            })
        return rows, lat, True
    except Exception as e:
        lat = (time.monotonic() - t0) * 1000
        rows = [{
            "device": str(g), "technology": "emporia", "alias": c["alias"], "is_on": "",
            "watts": "", "today_wh": "", "latency_ms": round(lat, 1),
            "error_msg": str(e)[:90], "http_status": http_code(e), **blank_extra(),
        } for g, c in EMPORIA_DEVICES.items()]
        return rows, lat, False


# ----------------------------- Shelly ---------------------------------
def _shelly_status_call():
    base = SHELLY_SERVER if (SHELLY_SERVER or "").startswith("http") else f"https://{SHELLY_SERVER}"
    data = urllib.parse.urlencode({"auth_key": SHELLY_AUTH_KEY}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/device/all_status", data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp), resp.status


def _shelly_full(status):
    """Extrae todos los campos útiles de un status Shelly (Gen2/3 + fallback Gen1)."""
    out = {"is_on": None, "watts": None, "voltage": "", "current": "", "freq": "",
           "temperature": "", "wifi_rssi": "", "aenergy_total": None}
    for k, v in status.items():
        if k.startswith("switch:") and isinstance(v, dict):
            out["is_on"] = v.get("output")
            out["watts"] = _n(v.get("apower"))
            out["voltage"] = _n(v.get("voltage"))
            out["current"] = _n(v.get("current"))
            out["freq"] = _n(v.get("freq"))
            out["temperature"] = _n((v.get("temperature") or {}).get("tC"))
            ae = v.get("aenergy") or {}
            if ae.get("total") is not None:
                out["aenergy_total"] = float(ae["total"])
            break
    if out["is_on"] is None and isinstance(status.get("relays"), list) and status["relays"]:
        out["is_on"] = status["relays"][0].get("ison")
    if out["watts"] is None and isinstance(status.get("meters"), list) and status["meters"]:
        out["watts"] = _n(status["meters"][0].get("power"))
        if out["aenergy_total"] is None and status["meters"][0].get("total") is not None:
            out["aenergy_total"] = float(status["meters"][0]["total"]) / 60.0
    wifi = status.get("wifi") or {}
    if wifi.get("rssi") is not None:
        out["wifi_rssi"] = wifi.get("rssi")
    return out


def poll_shelly(baseline):
    """Devuelve (rows, latency_ms, ok). today_wh = delta de aenergy desde el inicio del día."""
    t0 = time.monotonic()
    try:
        payload, http = _shelly_status_call()
        lat = (time.monotonic() - t0) * 1000
        if not payload.get("isok"):
            raise RuntimeError(f"isok=false: {json.dumps(payload)[:80]}")
        devices = (payload.get("data") or {}).get("devices_status") or {}
        rows = []
        for dev_id, status in devices.items():
            cfg = SHELLY_DEVICES.get(dev_id)
            if not cfg:
                continue
            ext = _shelly_full(status)
            energy = ext["aenergy_total"]
            if isinstance(energy, (int, float)):
                base = baseline.setdefault(dev_id, energy)
                today_wh = round(max(0.0, energy - base), 2)
            else:
                today_wh = 0.0
            rows.append({
                "device": dev_id, "technology": "shelly", "alias": cfg["alias"],
                "is_on": int(bool(ext["is_on"])) if ext["is_on"] is not None else "",
                "watts": ext["watts"] if ext["watts"] != "" else 0.0, "today_wh": today_wh,
                "latency_ms": round(lat, 1), "error_msg": "", "http_status": http,
                "voltage": ext["voltage"], "current": ext["current"], "freq": ext["freq"],
                "temperature": ext["temperature"], "wifi_rssi": ext["wifi_rssi"],
                "aenergy_total": round(energy, 3) if isinstance(energy, (int, float)) else "",
            })
        return rows, lat, True
    except Exception as e:
        lat = (time.monotonic() - t0) * 1000
        rows = [{
            "device": i, "technology": "shelly", "alias": c["alias"], "is_on": "",
            "watts": "", "today_wh": "", "latency_ms": round(lat, 1),
            "error_msg": str(e)[:90], "http_status": http_code(e), **blank_extra(),
        } for i, c in SHELLY_DEVICES.items()]
        return rows, lat, False


# ----------------------------- manifest -------------------------------
def update_manifest():
    bdir = ROOT / "energy_logs" / "benchmark"
    daily = sorted(p.name for p in bdir.glob("*.csv")
                   if p.name[:1].isdigit() and not p.name.startswith("resilience"))
    resilience = sorted(p.name for p in bdir.glob("resilience_test_*.csv"))
    (bdir / "manifest.json").write_text(json.dumps(
        {"updated": now_bogota().isoformat(timespec="seconds"),
         "files": daily, "resilience": resilience}, ensure_ascii=False))


# ----------------------------- resumen --------------------------------
def new_stats():
    return {"ok": 0, "fail": 0, "watts": [], "lat": [], "errors": set(), "http": Counter()}


def _accumulate(st, rows):
    for r in rows:
        if r["error_msg"]:
            st["fail"] += 1
            st["errors"].add(r["error_msg"][:40])
        else:
            st["ok"] += 1
            if isinstance(r["watts"], (int, float)):
                st["watts"].append(r["watts"])
        if r["http_status"] != "":
            st["http"][str(r["http_status"])] += 1


def print_summary(stats, polls, started):
    console.rule("[bold]Resumen del benchmark continuo[/bold]")
    dur = now_bogota() - started
    console.print(f"Polls: {polls} · duración: {str(dur).split('.')[0]}\n")
    t = Table(show_header=True, header_style="bold")
    t.add_column("Métrica"); t.add_column("Emporia", justify="right"); t.add_column("Shelly", justify="right")
    e, s = stats["emporia"], stats["shelly"]

    def lat_mean(x): return f"{statistics.mean(x['lat']):.0f} ms" if x["lat"] else "—"
    def lat_mm(x): return f"{min(x['lat']):.0f}–{max(x['lat']):.0f} ms" if x["lat"] else "—"
    def lat_sd(x): return f"{safe_stdev(x['lat']):.0f} ms" if x["lat"] else "—"
    def wr(x): return f"{min(x['watts']):.2f}–{max(x['watts']):.2f} W" if x["watts"] else "—"
    def ws(x): return f"{statistics.mean(x['watts']):.2f} ± {safe_stdev(x['watts']):.2f} W" if x["watts"] else "—"
    def http(x): return ", ".join(f"{k}×{v}" for k, v in x["http"].most_common()) or "—"
    def errs(x): return ", ".join(sorted(x["errors"])) or "—"

    t.add_row("Lecturas OK", str(e["ok"]), str(s["ok"]))
    t.add_row("Lecturas fallidas", str(e["fail"]), str(s["fail"]))
    t.add_row("[bold]Latencia[/bold] · promedio", lat_mean(e), lat_mean(s))
    t.add_row("Latencia · min–max", lat_mm(e), lat_mm(s))
    t.add_row("Latencia · σ", lat_sd(e), lat_sd(s))
    t.add_row("[bold]Sensibilidad[/bold] · rango watts", wr(e), wr(s))
    t.add_row("Consistencia · watts (media ± σ)", ws(e), ws(s))
    t.add_row("HTTP status", http(e), http(s))
    t.add_row("Errores", errs(e), errs(s))
    console.print(t)


# ------------------------------- main ---------------------------------
def main(args):
    global running
    interval = 2 if args.quick else args.interval
    console.rule("[bold]Benchmark continuo Emporia vs Shelly[/bold]")
    have_emp = bool(EMPORIA_EMAIL and EMPORIA_PASSWORD)
    have_she = bool(SHELLY_SERVER and SHELLY_AUTH_KEY)
    if not (have_emp or have_she):
        console.print("[red]No hay credenciales de ninguna tecnología.[/red]")
        return 1

    vue = None
    if have_emp:
        try:
            vue = emporia_connect()
        except Exception as ex:
            console.print(f"[red]Login Emporia falló:[/red] {ex} — se omite Emporia.")
            have_emp = False

    signal.signal(signal.SIGINT, _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)

    stats = {"emporia": new_stats(), "shelly": new_stats()}
    baseline = {}
    started = now_bogota()
    console.print(f"Lectura cada {interval}s · CSV por día · Ctrl+C para detener\n")

    current_date, fh, writer = None, None, None
    polls = 0
    try:
        while running:
            today = now_bogota().strftime("%Y-%m-%d")
            if today != current_date:
                if fh:
                    fh.close()
                current_date = today
                out_dir = ROOT / "energy_logs" / "benchmark"
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / f"{today}.csv"
                fresh = not out_path.exists() or out_path.stat().st_size == 0
                fh = open(out_path, "a", newline="")
                writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
                if fresh:
                    writer.writeheader()
                baseline.clear()  # reinicia el baseline de today_wh para el nuevo día
                update_manifest()
                console.print(f"[dim]→ CSV del día: {out_path.name}[/dim]")

            ts = now_bogota().isoformat(timespec="seconds")
            polls += 1
            last = {"emporia": (0, 0), "shelly": (0, 0)}

            if have_emp:
                rows, lat, ok = poll_emporia(vue)
                for r in rows:
                    writer.writerow({"timestamp": ts, **r})
                _accumulate(stats["emporia"], rows)
                stats["emporia"]["lat"].append(lat)
                last["emporia"] = (lat, sum(1 for r in rows if r["error_msg"]))
                if not ok:
                    try:
                        vue = emporia_connect()
                    except Exception:
                        pass

            if have_she:
                rows, lat, ok = poll_shelly(baseline)
                for r in rows:
                    writer.writerow({"timestamp": ts, **r})
                _accumulate(stats["shelly"], rows)
                stats["shelly"]["lat"].append(lat)
                last["shelly"] = (lat, sum(1 for r in rows if r["error_msg"]))

            fh.flush()
            if polls == 1 or polls % 20 == 0:
                e, s = stats["emporia"], stats["shelly"]
                console.print(
                    f"[dim]#{polls}[/dim] {ts[11:19]} · "
                    f"EMP ok={e['ok']} fail={e['fail']} {last['emporia'][0]:.0f}ms · "
                    f"SHE ok={s['ok']} fail={s['fail']} {last['shelly'][0]:.0f}ms"
                )

            if args.max_polls and polls >= args.max_polls:
                break
            sleep_interruptible(interval)
    finally:
        if fh:
            fh.close()

    print_summary(stats, polls, started)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Benchmark continuo Emporia vs Shelly")
    p.add_argument("--interval", type=int, default=30, help="segundos entre lecturas (default 30)")
    p.add_argument("--max-polls", type=int, default=0, help="máximo de lecturas (0 = indefinido)")
    p.add_argument("--quick", action="store_true", help="intervalo 2s para prueba")
    args = p.parse_args()
    sys.exit(main(args))
