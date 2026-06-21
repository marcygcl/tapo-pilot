"""Prueba de conexión con Shelly Cloud.

Lee SHELLY_SERVER y SHELLY_AUTH_KEY del .env, llama al endpoint cloud
/device/all_status y muestra para cada dispositivo: nombre, id, online,
is_on y watts en tiempo real.

Uso:
    uv run python collectors/shelly/test_shelly.py

Requiere en el .env:
    SHELLY_SERVER=https://shelly-XX-eu.shelly.cloud
    SHELLY_AUTH_KEY=...

API (Shelly Cloud Control):
    POST https://{server}/device/all_status
    Headers: Content-Type: application/x-www-form-urlencoded
    Body:    auth_key={key}
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

# El .env está en la raíz del repo (este script vive en collectors/shelly/).
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

SERVER = os.getenv("SHELLY_SERVER")
AUTH_KEY = os.getenv("SHELLY_AUTH_KEY")

console = Console()


def extract(status):
    """Saca (nombre, online, is_on, watts) de un status de dispositivo.

    Shelly devuelve estructuras distintas según la generación del dispositivo:
      - Gen1 (p.ej. Plug S): relays[].ison + meters[].power
      - Gen2/3 (Plus/Pro):   switch:N.output + switch:N.apower
    """
    info = status.get("_dev_info", {}) or {}

    online = info.get("online")
    if online is None:
        online = (status.get("cloud") or {}).get("connected")

    is_on, watts = None, None
    # Gen2/3: cualquier canal "switch:N"
    for k, v in status.items():
        if k.startswith("switch:") and isinstance(v, dict):
            is_on = v.get("output")
            watts = v.get("apower")
            break
    # Gen1: relays + meters/emeters
    if is_on is None and isinstance(status.get("relays"), list) and status["relays"]:
        is_on = status["relays"][0].get("ison")
    if watts is None and isinstance(status.get("meters"), list) and status["meters"]:
        watts = status["meters"][0].get("power")
    if watts is None and isinstance(status.get("emeters"), list) and status["emeters"]:
        watts = status["emeters"][0].get("power")

    # all_status casi nunca trae el nombre asignado por el usuario; dejamos un
    # fallback al code de modelo (el nombre real se resuelve aparte, ver fetch_names).
    name = status.get("name") or info.get("name") or info.get("code") or status.get("code")
    return name, online, is_on, watts


def _post(base, path, auth_key):
    """POST x-www-form-urlencoded con auth_key al cloud de Shelly; devuelve el JSON."""
    data = urllib.parse.urlencode({"auth_key": auth_key}).encode()
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_names(base, auth_key):
    """Mapa {id: nombre} desde /interface/device/list (best-effort)."""
    try:
        payload = _post(base, "/interface/device/list", auth_key)
        devices = (payload.get("data") or {}).get("devices") or {}
        return {did: (d.get("name") or "").strip() for did, d in devices.items()}
    except Exception:
        return {}


def fmt_bool(v):
    if v is None:
        return "—"
    return "[green]sí[/green]" if v else "[red]no[/red]"


def main():
    if not SERVER or not AUTH_KEY:
        console.print("[red]ERROR:[/red] faltan SHELLY_SERVER / SHELLY_AUTH_KEY en el .env")
        console.print("       Ver .env.example y completarlos.")
        return 1

    base = SERVER if SERVER.startswith("http") else f"https://{SERVER}"
    url = base.rstrip("/") + "/device/all_status"

    data = urllib.parse.urlencode({"auth_key": AUTH_KEY}).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )

    console.print(f"POST {url} ...")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except Exception as e:
        console.print(f"[red]ERROR llamando a Shelly cloud:[/red] {e}")
        return 1

    if not payload.get("isok"):
        console.print(f"[red]ERROR: respuesta no OK:[/red] {json.dumps(payload)[:500]}")
        return 1

    devices = (payload.get("data") or {}).get("devices_status") or {}
    console.print(f"Dispositivos encontrados: {len(devices)}\n")
    if not devices:
        return 0

    names = fetch_names(base, AUTH_KEY)  # nombres reales (best-effort)

    table = Table(title="Shelly Cloud — estado de dispositivos")
    table.add_column("Nombre", style="bold")
    table.add_column("ID")
    table.add_column("Online")
    table.add_column("Encendido")
    table.add_column("Watts", justify="right")

    for dev_id, status in devices.items():
        name, online, is_on, watts = extract(status)
        display_name = names.get(dev_id) or name or "—"
        table.add_row(
            str(display_name),
            dev_id,
            fmt_bool(online),
            fmt_bool(is_on),
            f"{watts:.1f}" if isinstance(watts, (int, float)) else "—",
        )

    console.print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
