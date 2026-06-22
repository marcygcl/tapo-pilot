"""Benchmark comparativo Emporia vs Shelly.

Corre una lectura de ambas tecnologías cada 30s durante 5 min (configurable),
mide la latencia de cada API, guarda todas las lecturas en
energy_logs/benchmark/YYYY-MM-DD-HH-MM-SS.csv y al final imprime un resumen
comparativo (sensibilidad, latencia, consistencia/gaps).

Uso:
    uv run python collectors/benchmark.py                 # 30s x 5 min (default)
    uv run python collectors/benchmark.py --interval 30 --duration 300
    uv run python collectors/benchmark.py --interval 2 --duration 6   # prueba rápida

CSV: timestamp, device, technology, alias, is_on, watts, today_wh, latency_ms
"""

import argparse
import csv
import datetime
import json
import statistics
import sys
import time
import urllib.parse
import urllib.request
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
CSV_FIELDS = ["timestamp", "device", "technology", "alias", "is_on", "watts", "today_wh", "latency_ms"]
TOKEN_FILE = str(Path(__file__).resolve().parent / "emporia" / ".emporia_tokens.json")
MAIN_CHANNEL = "1,2,3"


# ----------------------------- utilidades -----------------------------
def safe_stdev(xs):
    return statistics.pstdev(xs) if len(xs) >= 1 else None


def fnum(v, nd=1):
    return f"{v:.{nd}f}" if isinstance(v, (int, float)) else "—"


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


def emporia_read(vue):
    """Devuelve (rows, latency_ms). rows: lista de dicts por dispositivo Emporia."""
    from pyemvue.enums import Scale, Unit
    # Reintentos acotados: pyemvue por defecto reintenta 5x con backoff (hasta ~60s)
    # cuando el dato del segundo aún no llegó; eso distorsiona la latencia medida.
    retry = dict(max_retry_attempts=2, initial_retry_delay=0.5, max_retry_delay=2.0)
    t0 = time.monotonic()
    devices = vue.get_devices()
    gids = [d.device_gid for d in devices]
    now = datetime.datetime.now(datetime.timezone.utc)
    watt = vue.get_device_list_usage(gids, now, scale=Scale.SECOND.value, unit=Unit.KWH.value, **retry)
    day = vue.get_device_list_usage(gids, now, scale=Scale.DAY.value, unit=Unit.KWH.value, **retry)
    outlets = {o.device_gid: o.outlet_on for o in vue.get_outlets()}
    latency_ms = (time.monotonic() - t0) * 1000

    rows = []
    for d in devices:
        gid = d.device_gid
        cfg = EMPORIA_DEVICES.get(gid)
        if not cfg:
            continue  # solo dispositivos configurados (one/two/three/four)
        ch_w = _emp_channel(watt.get(gid))
        ch_d = _emp_channel(day.get(gid))
        watts = round((ch_w.usage or 0.0) * 3600 * 1000, 2) if ch_w else 0.0
        today_wh = round((ch_d.usage or 0.0) * 1000, 1) if ch_d else 0.0
        is_on = outlets.get(gid)
        if is_on is None and d.outlet is not None:
            is_on = d.outlet.outlet_on
        rows.append({
            "device": str(gid), "alias": cfg["alias"],
            "is_on": int(bool(is_on)) if is_on is not None else "",
            "watts": watts, "today_wh": today_wh,
        })
    return rows, latency_ms


# ----------------------------- Shelly ---------------------------------
def _shelly_post(path):
    base = SHELLY_SERVER if (SHELLY_SERVER or "").startswith("http") else f"https://{SHELLY_SERVER}"
    data = urllib.parse.urlencode({"auth_key": SHELLY_AUTH_KEY}).encode()
    req = urllib.request.Request(
        base.rstrip("/") + path, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _shelly_extract(status):
    """(is_on, watts, energy_wh_total) soportando Gen1 y Gen2/3."""
    is_on, watts, energy = None, None, None
    for k, v in status.items():
        if k.startswith("switch:") and isinstance(v, dict):
            is_on = v.get("output")
            watts = v.get("apower")
            ae = v.get("aenergy") or {}
            if ae.get("total") is not None:
                energy = float(ae["total"])
            break
    if is_on is None and isinstance(status.get("relays"), list) and status["relays"]:
        is_on = status["relays"][0].get("ison")
    if isinstance(status.get("meters"), list) and status["meters"]:
        m = status["meters"][0]
        if watts is None:
            watts = m.get("power")
        if energy is None and m.get("total") is not None:
            energy = float(m["total"]) / 60.0
    return is_on, watts, energy


def shelly_read(baseline):
    """Devuelve (rows, latency_ms). today_wh = delta de energía desde el inicio del benchmark."""
    t0 = time.monotonic()
    payload = _shelly_post("/device/all_status")
    latency_ms = (time.monotonic() - t0) * 1000
    if not payload.get("isok"):
        raise RuntimeError(f"respuesta no OK: {json.dumps(payload)[:200]}")
    devices = (payload.get("data") or {}).get("devices_status") or {}

    rows = []
    for dev_id, status in devices.items():
        cfg = SHELLY_DEVICES.get(dev_id)
        if not cfg:
            continue  # solo dispositivos configurados (shelly_0/shelly_1)
        is_on, watts, energy = _shelly_extract(status)
        if energy is not None:
            base = baseline.setdefault(dev_id, energy)
            today_wh = round(max(0.0, energy - base), 2)
        else:
            today_wh = 0.0
        rows.append({
            "device": dev_id, "alias": cfg["alias"],
            "is_on": int(bool(is_on)) if is_on is not None else "",
            "watts": round(float(watts), 2) if isinstance(watts, (int, float)) else 0.0,
            "today_wh": today_wh,
        })
    return rows, latency_ms


# ----------------------------- resumen --------------------------------
def print_summary(stats, n_cycles, out_path):
    console.rule("[bold]Resumen comparativo[/bold]")
    t = Table(show_header=True, header_style="bold")
    t.add_column("Métrica")
    t.add_column("Emporia", justify="right")
    t.add_column("Shelly", justify="right")

    def col(tech, fn):
        s = stats[tech]
        return fn(s)

    def expected(s):
        return n_cycles * s["n_devices"]

    # Lecturas / gaps
    t.add_row("Dispositivos", str(stats["emporia"]["n_devices"]), str(stats["shelly"]["n_devices"]))
    t.add_row("Lecturas OK / esperadas",
              f'{stats["emporia"]["ok"]}/{expected(stats["emporia"])}',
              f'{stats["shelly"]["ok"]}/{expected(stats["shelly"])}')
    t.add_row("[bold]Consistencia[/bold] · gaps (perdidas)",
              str(expected(stats["emporia"]) - stats["emporia"]["ok"]),
              str(expected(stats["shelly"]) - stats["shelly"]["ok"]))

    # Sensibilidad (rango de watts)
    def wrange(s):
        ws = s["watts"]
        return f"{min(ws):.2f} – {max(ws):.2f} W" if ws else "—"
    def wspan(s):
        ws = s["watts"]
        return f"{(max(ws)-min(ws)):.2f} W" if ws else "—"
    t.add_row("[bold]Sensibilidad[/bold] · rango watts", wrange(stats["emporia"]), wrange(stats["shelly"]))
    t.add_row("Sensibilidad · amplitud", wspan(stats["emporia"]), wspan(stats["shelly"]))

    # Consistencia (desv. estándar de watts)
    def wstats(s):
        ws = s["watts"]
        if not ws:
            return "—"
        return f"{statistics.mean(ws):.2f} ± {safe_stdev(ws):.2f} W"
    t.add_row("Consistencia · watts (media ± σ)", wstats(stats["emporia"]), wstats(stats["shelly"]))

    # Latencia
    def lat_mean(s):
        return f"{statistics.mean(s['lat']):.0f} ms" if s["lat"] else "—"
    def lat_minmax(s):
        return f"{min(s['lat']):.0f} – {max(s['lat']):.0f} ms" if s["lat"] else "—"
    def lat_std(s):
        return f"{safe_stdev(s['lat']):.0f} ms" if s["lat"] else "—"
    t.add_row("[bold]Latencia[/bold] · promedio", lat_mean(stats["emporia"]), lat_mean(stats["shelly"]))
    t.add_row("Latencia · min–max", lat_minmax(stats["emporia"]), lat_minmax(stats["shelly"]))
    t.add_row("Latencia · σ", lat_std(stats["emporia"]), lat_std(stats["shelly"]))

    console.print(t)
    console.print(f"\n[dim]CSV guardado en:[/dim] {out_path}")


# ----------------------------- main -----------------------------------
def main(interval, duration):
    console.rule("[bold]Benchmark Emporia vs Shelly[/bold]")
    have_emp = bool(EMPORIA_EMAIL and EMPORIA_PASSWORD)
    have_she = bool(SHELLY_SERVER and SHELLY_AUTH_KEY)
    if not have_emp:
        console.print("[yellow]Aviso: faltan credenciales Emporia; se omite esa tecnología.[/yellow]")
    if not have_she:
        console.print("[yellow]Aviso: faltan credenciales Shelly; se omite esa tecnología.[/yellow]")
    if not (have_emp or have_she):
        console.print("[red]No hay credenciales de ninguna tecnología. Nada que medir.[/red]")
        return 1

    ts0 = now_bogota()
    out_dir = ROOT / "energy_logs" / "benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{ts0.strftime('%Y-%m-%d-%H-%M-%S')}.csv"

    vue = None
    if have_emp:
        try:
            vue = emporia_connect()
        except Exception as e:
            console.print(f"[red]Login Emporia falló:[/red] {e}")
            have_emp = False

    stats = {
        "emporia": {"watts": [], "lat": [], "ok": 0, "n_devices": len(EMPORIA_DEVICES) if have_emp else 0},
        "shelly": {"watts": [], "lat": [], "ok": 0, "n_devices": len(SHELLY_DEVICES) if have_she else 0},
    }
    shelly_baseline = {}

    console.print(f"Intervalo {interval}s · duración {duration}s · CSV: {out_path.name}\n")
    f = open(out_path, "w", newline="")
    writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
    writer.writeheader()

    cycle = 0
    start = time.monotonic()
    try:
        while True:
            cycle += 1
            ts = now_bogota().isoformat(timespec="seconds")
            line = f"[dim]#{cycle}[/dim] {ts[11:19]}"

            # --- Emporia ---
            if have_emp:
                try:
                    rows, lat = emporia_read(vue)
                    for r in rows:
                        writer.writerow({"timestamp": ts, "technology": "emporia",
                                         "latency_ms": round(lat, 1), **r})
                        stats["emporia"]["ok"] += 1
                        if isinstance(r["watts"], (int, float)):
                            stats["emporia"]["watts"].append(r["watts"])
                    stats["emporia"]["lat"].append(lat)
                    line += f" · [green]EMP[/green] {len(rows)}dev {lat:.0f}ms"
                except Exception as e:
                    line += f" · [red]EMP error[/red] ({str(e)[:40]})"
                    try:
                        vue = emporia_connect()  # reintento de sesión
                    except Exception:
                        pass

            # --- Shelly ---
            if have_she:
                try:
                    rows, lat = shelly_read(shelly_baseline)
                    for r in rows:
                        writer.writerow({"timestamp": ts, "technology": "shelly",
                                         "latency_ms": round(lat, 1), **r})
                        stats["shelly"]["ok"] += 1
                        if isinstance(r["watts"], (int, float)):
                            stats["shelly"]["watts"].append(r["watts"])
                    stats["shelly"]["lat"].append(lat)
                    line += f" · [cyan]SHE[/cyan] {len(rows)}dev {lat:.0f}ms"
                except Exception as e:
                    line += f" · [red]SHE error[/red] ({str(e)[:40]})"

            f.flush()
            console.print(line)

            elapsed = time.monotonic() - start
            remaining = duration - elapsed
            if remaining <= 0:
                break
            time.sleep(min(interval, remaining))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrumpido — generando resumen con lo recolectado...[/yellow]")
    finally:
        f.close()

    print_summary(stats, cycle, out_path)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Benchmark Emporia vs Shelly")
    p.add_argument("--interval", type=int, default=30, help="segundos entre lecturas (default 30)")
    p.add_argument("--duration", type=int, default=300, help="duración total en segundos (default 300)")
    args = p.parse_args()
    sys.exit(main(args.interval, args.duration))
