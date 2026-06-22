"""Test de resiliencia Emporia vs Shelly ante caídas de internet.

Corre 4 fases cronometradas y registra cómo se comporta cada API cuando se
desconecta/reconecta el router (y opcionalmente al apagar/prender un plug):

  FASE 1 · BASELINE  (default 3 min) — lectura normal con internet OK
  FASE 2 · OUTAGE    (default 2 min) — DESCONECTA el router; mide time-to-fail
  FASE 3 · RECOVERY  (default 2 min) — RECONECTA el router; mide time-to-recover
  FASE 4 · DEVICE    (default 2 min) — (opcional) toggle de un plug + outage/recovery

El script NO controla el router: imprime instrucciones al inicio de cada fase y
sigue intentando leer las APIs cada `--interval` segundos, registrando todo.

CSV: energy_logs/benchmark/resilience_test_YYYY-MM-DD-HH-MM-SS.csv
     timestamp, phase, technology, alias, status, watts, error_msg, recovery_time_s

Uso:
    uv run python collectors/discover_resilience.py
    uv run python collectors/discover_resilience.py --quick            # fases cortas (prueba)
    uv run python collectors/discover_resilience.py --toggle-device    # apaga/prende 'one' (¡efecto real!)

Maneja desconexiones gracefully: nunca aborta, sigue intentando y resume al final.
"""

import argparse
import csv
import datetime
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
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
CSV_FIELDS = ["timestamp", "phase", "technology", "alias", "status",
              "watts", "error_msg", "recovery_time_s"]
TOKEN_FILE = str(Path(__file__).resolve().parent / "emporia" / ".emporia_tokens.json")
HTTP_TIMEOUT = 10  # s — para que la falla durante el outage no cuelgue indefinidamente

EMP_ALIASES = [c["alias"] for c in EMPORIA_DEVICES.values()]
SHE_ALIASES = [c["alias"] for c in SHELLY_DEVICES.values()]


def classify_error(e):
    s = str(e).lower()
    if isinstance(e, (socket.timeout, TimeoutError)) or "timed out" in s or "timeout" in s:
        return "TIMEOUT"
    net = ("name or service not known", "temporary failure in name resolution",
           "failed to resolve", "nodename nor servname", "connection refused",
           "network is unreachable", "no route to host", "connection reset",
           "max retries", "newconnectionerror", "connectionerror")
    if isinstance(e, (urllib.error.URLError, ConnectionError, socket.gaierror)) or any(k in s for k in net):
        return "OFFLINE"
    return "ERROR"


# ------------------------------ Emporia ------------------------------
def emporia_connect():
    from pyemvue import PyEmVue
    vue = PyEmVue()
    vue.login(username=EMPORIA_EMAIL, password=EMPORIA_PASSWORD, token_storage_file=TOKEN_FILE)
    return vue


def _emp_watts(usage_device):
    if not usage_device or not usage_device.channels:
        return 0.0
    ch = usage_device.channels.get("1,2,3") or next(iter(usage_device.channels.values()), None)
    return round((ch.usage or 0.0) * 3600 * 1000, 2) if ch else 0.0


def poll_emporia(vue):
    """Devuelve (results, latency_ms). results: lista de {alias, ok, watts, error, errclass}."""
    from pyemvue.enums import Scale, Unit
    retry = dict(max_retry_attempts=2, initial_retry_delay=0.5, max_retry_delay=2.0)
    t0 = time.monotonic()
    try:
        devices = vue.get_devices()
        gids = [d.device_gid for d in devices]
        now = datetime.datetime.now(datetime.timezone.utc)
        watt = vue.get_device_list_usage(gids, now, scale=Scale.SECOND.value, unit=Unit.KWH.value, **retry)
        outlets = {o.device_gid: o.outlet_on for o in vue.get_outlets()}
        lat = (time.monotonic() - t0) * 1000
        results = []
        for d in devices:
            cfg = EMPORIA_DEVICES.get(d.device_gid)
            if not cfg:
                continue
            results.append({"alias": cfg["alias"], "ok": True,
                            "watts": _emp_watts(watt.get(d.device_gid)), "error": "", "errclass": ""})
        return results, lat
    except Exception as e:
        lat = (time.monotonic() - t0) * 1000
        cls = classify_error(e)
        return [{"alias": a, "ok": False, "watts": "", "error": str(e)[:70], "errclass": cls}
                for a in EMP_ALIASES], lat


def emporia_toggle(vue, alias):
    """Apaga/prende el plug Emporia con ese alias (efecto físico real). Devuelve nuevo estado."""
    gid = next((g for g, c in EMPORIA_DEVICES.items() if c["alias"] == alias), None)
    if gid is None:
        raise RuntimeError(f"alias {alias} no encontrado")
    outlet = next((o for o in vue.get_outlets() if o.device_gid == gid), None)
    if outlet is None:
        raise RuntimeError(f"outlet {alias} no disponible")
    new_state = not outlet.outlet_on
    vue.update_outlet(outlet, new_state)
    return new_state


# ------------------------------ Shelly -------------------------------
def _shelly_post(path):
    base = SHELLY_SERVER if (SHELLY_SERVER or "").startswith("http") else f"https://{SHELLY_SERVER}"
    data = urllib.parse.urlencode({"auth_key": SHELLY_AUTH_KEY}).encode()
    req = urllib.request.Request(base.rstrip("/") + path, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.load(resp)


def _shelly_watts(status):
    for k, v in status.items():
        if k.startswith("switch:") and isinstance(v, dict):
            w = v.get("apower")
            return round(float(w), 2) if isinstance(w, (int, float)) else 0.0
    if isinstance(status.get("meters"), list) and status["meters"]:
        w = status["meters"][0].get("power")
        return round(float(w), 2) if isinstance(w, (int, float)) else 0.0
    return 0.0


def poll_shelly():
    t0 = time.monotonic()
    try:
        payload = _shelly_post("/device/all_status")
        lat = (time.monotonic() - t0) * 1000
        if not payload.get("isok"):
            raise RuntimeError(f"no OK: {json.dumps(payload)[:60]}")
        ds = (payload.get("data") or {}).get("devices_status") or {}
        results = []
        for dev_id, status in ds.items():
            cfg = SHELLY_DEVICES.get(dev_id)
            if not cfg:
                continue
            results.append({"alias": cfg["alias"], "ok": True,
                            "watts": _shelly_watts(status), "error": "", "errclass": ""})
        return results, lat
    except Exception as e:
        lat = (time.monotonic() - t0) * 1000
        cls = classify_error(e)
        return [{"alias": a, "ok": False, "watts": "", "error": str(e)[:70], "errclass": cls}
                for a in SHE_ALIASES], lat


# ------------------------------ estado -------------------------------
class Tracker:
    """Lleva estado por dispositivo para detectar fail/recovery y acumular métricas."""
    def __init__(self):
        self.failing = defaultdict(bool)        # alias -> en falla
        self.fail_start = {}                     # alias -> monotonic del inicio de falla
        self.stats = {
            "emporia": {"ok": 0, "fail": 0, "fail_lat": [], "recovery": [], "errors": set(),
                        "by_phase": defaultdict(lambda: [0, 0])},
            "shelly": {"ok": 0, "fail": 0, "fail_lat": [], "recovery": [], "errors": set(),
                       "by_phase": defaultdict(lambda: [0, 0])},
        }

    def update(self, tech, phase, r, latency):
        st = self.stats[tech]
        key = (tech, r["alias"])
        if r["ok"]:
            st["ok"] += 1
            st["by_phase"][phase][0] += 1
            if self.failing[key]:
                rec = round(time.monotonic() - self.fail_start.get(key, time.monotonic()), 1)
                st["recovery"].append(rec)
                self.failing[key] = False
                return "RECOVERING", rec
            return "OK", ""
        else:
            st["fail"] += 1
            st["by_phase"][phase][1] += 1
            st["errors"].add(r["errclass"])
            st["fail_lat"].append(latency)
            if not self.failing[key]:
                self.failing[key] = True
                self.fail_start[key] = time.monotonic()
            return r["errclass"], ""


# ------------------------------ fases --------------------------------
def banner(text, style="bold yellow"):
    console.print()
    console.rule(f"[{style}]{text}[/{style}]")


def run_phase(phase, duration, interval, ctx, writer, tracker):
    end = time.monotonic() + duration
    while True:
        ts = now_bogota().isoformat(timespec="seconds")
        line = f"[dim]{ts[11:19]}[/dim] [{phase}]"
        for tech, poll in (("emporia", ctx["emp_poll"]), ("shelly", poll_shelly)):
            if not ctx[f"{tech}_enabled"]:
                continue
            results, lat = poll()
            ok = sum(1 for r in results if r["ok"])
            for r in results:
                status, rec = tracker.update(tech, phase, r, lat)
                writer.writerow({
                    "timestamp": ts, "phase": phase, "technology": tech, "alias": r["alias"],
                    "status": status, "watts": r["watts"], "error_msg": r["error"],
                    "recovery_time_s": rec,
                })
            tag = "green" if ok == len(results) else ("red" if ok == 0 else "yellow")
            line += f" · [{tag}]{tech[:3].upper()} {ok}/{len(results)} {lat:.0f}ms[/{tag}]"
        console.print(line)
        ctx["fh"].flush()
        if time.monotonic() >= end:
            break
        time.sleep(min(interval, max(0, end - time.monotonic())))


# ----------------------------- resumen -------------------------------
def print_summary(tracker, toggle_info, out_path):
    banner("RESUMEN DE RESILIENCIA", "bold cyan")
    s = tracker.stats
    t = Table(show_header=True, header_style="bold")
    t.add_column("Métrica")
    t.add_column("Emporia", justify="right")
    t.add_column("Shelly", justify="right")

    def avg(xs):
        return f"{sum(xs) / len(xs):.1f}" if xs else "—"

    t.add_row("Lecturas OK", str(s["emporia"]["ok"]), str(s["shelly"]["ok"]))
    t.add_row("Lecturas fallidas", str(s["emporia"]["fail"]), str(s["shelly"]["fail"]))
    t.add_row("[bold]Time to fail[/bold] · latencia media falla (ms)",
              avg(s["emporia"]["fail_lat"]), avg(s["shelly"]["fail_lat"]))
    t.add_row("[bold]Time to recover[/bold] · media (s)",
              avg(s["emporia"]["recovery"]), avg(s["shelly"]["recovery"]))
    t.add_row("Recoveries detectados",
              str(len(s["emporia"]["recovery"])), str(len(s["shelly"]["recovery"])))
    t.add_row("Tipos de error",
              ", ".join(sorted(s["emporia"]["errors"])) or "—",
              ", ".join(sorted(s["shelly"]["errors"])) or "—")
    # Integridad: OK por fase (esperamos OK en BASELINE y RECOVERY; fallas en OUTAGE)
    for ph in ("BASELINE", "OUTAGE", "RECOVERY", "DEVICE"):
        e = s["emporia"]["by_phase"].get(ph, [0, 0])
        sh = s["shelly"]["by_phase"].get(ph, [0, 0])
        t.add_row(f"  {ph}: OK/fail", f"{e[0]}/{e[1]}", f"{sh[0]}/{sh[1]}")
    console.print(t)

    # Integridad de datos / diferencias
    console.print("\n[bold]Integridad de datos:[/bold]")
    console.print("  • Ambas APIs son cloud: tras reconectar devuelven el [b]estado actual[/b].")
    console.print("  • Emporia: el medidor bufferiza localmente y el cloud rellena el histórico "
                  "(today_wh sigue creciendo). Shelly: el contador aenergy.total es acumulado en "
                  "el dispositivo, no se pierde, pero la nube puede no rellenar segundos perdidos.")
    if toggle_info:
        console.print(f"\n[bold]Sincronización de estado (FASE 4):[/bold] {toggle_info}")

    console.print(f"\n[dim]CSV:[/dim] {out_path}")


# ------------------------------- main --------------------------------
def main(args):
    banner("TEST DE RESILIENCIA — Emporia vs Shelly", "bold cyan")
    have_emp = bool(EMPORIA_EMAIL and EMPORIA_PASSWORD)
    have_she = bool(SHELLY_SERVER and SHELLY_AUTH_KEY)
    if not (have_emp or have_she):
        console.print("[red]No hay credenciales de ninguna tecnología.[/red]")
        return 1

    if args.quick:
        d_base = d_out = d_rec = d_dev = 4
        interval = 2
    else:
        d_base, d_out, d_rec, d_dev = args.baseline, args.outage, args.recovery, args.toggle
        interval = args.interval

    out_dir = ROOT / "energy_logs" / "benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"resilience_test_{now_bogota().strftime('%Y-%m-%d-%H-%M-%S')}.csv"

    vue = None
    if have_emp:
        try:
            vue = emporia_connect()
        except Exception as e:
            console.print(f"[red]Login Emporia falló:[/red] {e} — se omite Emporia.")
            have_emp = False

    tracker = Tracker()
    fh = open(out_path, "w", newline="")
    writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
    writer.writeheader()
    ctx = {
        "emporia_enabled": have_emp, "shelly_enabled": have_she, "fh": fh,
        "emp_poll": (lambda: poll_emporia(vue)) if have_emp else (lambda: ([], 0.0)),
    }

    console.print(f"Dispositivos: Emporia {EMP_ALIASES if have_emp else '—'} · "
                  f"Shelly {SHE_ALIASES if have_she else '—'}")
    console.print(f"Fases (s): baseline={d_base} outage={d_out} recovery={d_rec} device={d_dev} · "
                  f"intervalo={interval}s · CSV={out_path.name}")

    toggle_info = ""
    try:
        # FASE 1
        banner("FASE 1 · BASELINE — internet OK (no toques nada)", "bold green")
        run_phase("BASELINE", d_base, interval, ctx, writer, tracker)

        # FASE 2
        banner("FASE 2 · OUTAGE — ⚠️  DESCONECTA EL ROUTER / INTERNET AHORA", "bold red")
        if not args.quick:
            console.print("[red]Tienes ~5s para desconectar...[/red]"); time.sleep(5)
        run_phase("OUTAGE", d_out, interval, ctx, writer, tracker)

        # FASE 3
        banner("FASE 3 · RECOVERY — ✅  RECONECTA EL ROUTER AHORA", "bold green")
        if not args.quick:
            console.print("[green]Tienes ~5s para reconectar...[/green]"); time.sleep(5)
        run_phase("RECOVERY", d_rec, interval, ctx, writer, tracker)

        # FASE 4
        banner("FASE 4 · DEVICE — toggle de plug + outage/recovery", "bold yellow")
        toggle_alias = "one" if "one" in EMP_ALIASES else (EMP_ALIASES[0] if EMP_ALIASES else None)
        if args.toggle_device and have_emp and toggle_alias:
            try:
                new_state = emporia_toggle(vue, toggle_alias)
                toggle_info = f"comandado '{toggle_alias}' -> {'ON' if new_state else 'OFF'}; "
                console.print(f"[yellow]Plug '{toggle_alias}' comandado a {'ON' if new_state else 'OFF'}.[/yellow]")
            except Exception as e:
                toggle_info = f"toggle falló: {e}; "
                console.print(f"[red]Toggle falló:[/red] {e}")
        else:
            console.print(f"[dim]Sin toggle automático (usa --toggle-device). "
                          f"Apaga/prende manualmente '{toggle_alias}' si quieres probar la sincronización.[/dim]")
        if not args.quick:
            console.print("[red]Desconecta el router de nuevo (~5s)...[/red]"); time.sleep(5)
        run_phase("DEVICE", d_dev, interval, ctx, writer, tracker)
        if not args.quick:
            console.print("[green]Reconecta el router.[/green]")
        # Verificar sincronización: ¿el estado leído refleja lo comandado?
        if args.toggle_device and have_emp:
            try:
                res, _ = poll_emporia(vue)
                cur = next((r for r in res if r["alias"] == toggle_alias), None)
                if cur and cur["ok"]:
                    toggle_info += f"estado leído tras recovery: watts={cur['watts']}"
            except Exception:
                pass
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrumpido — generando resumen con lo recolectado...[/yellow]")
    finally:
        fh.close()

    print_summary(tracker, toggle_info, out_path)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Test de resiliencia Emporia vs Shelly")
    p.add_argument("--baseline", type=int, default=180, help="duración FASE 1 (s, default 180)")
    p.add_argument("--outage", type=int, default=120, help="duración FASE 2 (s, default 120)")
    p.add_argument("--recovery", type=int, default=120, help="duración FASE 3 (s, default 120)")
    p.add_argument("--toggle", type=int, default=120, help="duración FASE 4 (s, default 120)")
    p.add_argument("--interval", type=int, default=30, help="segundos entre lecturas (default 30)")
    p.add_argument("--quick", action="store_true", help="fases cortas para prueba (4s, intervalo 2s)")
    p.add_argument("--toggle-device", action="store_true",
                   help="apaga/prende físicamente el plug 'one' en FASE 4 (¡efecto real!)")
    args = p.parse_args()
    sys.exit(main(args))
