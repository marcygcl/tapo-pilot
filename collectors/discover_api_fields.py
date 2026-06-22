"""Discovery de campos de API: explora qué expone CADA API nativa.

Para cada dispositivo (Emporia: one/two/three/four · Shelly: shelly_0/shelly_1):
  1. Consulta la API nativa (pyemvue para Emporia, /device/all_status para Shelly).
  2. Captura la respuesta COMPLETA (sin filtrar).
  3. Imprime el JSON formateado con todos los campos.

Luego imprime una tabla comparativa de todos los campos posibles (Emporia vs
Shelly, tipo y ejemplo) y guarda una respuesta cruda de muestra por tecnología en:
  energy_logs/benchmark/discovery_emporia_sample.json
  energy_logs/benchmark/discovery_shelly_sample.json

Uso:
    uv run python collectors/discover_api_fields.py
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
    EMPORIA_DEVICES,
    EMPORIA_EMAIL,
    EMPORIA_PASSWORD,
    ROOT,
    SHELLY_AUTH_KEY,
    SHELLY_DEVICES,
    SHELLY_SERVER,
)

console = Console()
TOKEN_FILE = str(Path(__file__).resolve().parent / "emporia" / ".emporia_tokens.json")
OUT_DIR = ROOT / "energy_logs" / "benchmark"


# --------------------------- serialización ---------------------------
def to_dict(o, _depth=0):
    """Serializa recursivamente cualquier objeto (incl. objetos pyemvue) a JSON-able."""
    if _depth > 12:
        return str(o)
    if o is None or isinstance(o, (str, int, float, bool)):
        return o
    if isinstance(o, datetime.datetime):
        return o.isoformat()
    if isinstance(o, dict):
        return {str(k): to_dict(v, _depth + 1) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [to_dict(x, _depth + 1) for x in o]
    if hasattr(o, "__dict__"):
        return {k: to_dict(v, _depth + 1) for k, v in vars(o).items()}
    return str(o)


def flatten(o, prefix=""):
    """Aplana un dict/list anidado a {ruta.de.campo: valor_hoja}."""
    out = {}
    if isinstance(o, dict):
        if not o:
            out[prefix or "{}"] = {}
        for k, v in o.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            out.update(flatten(v, p))
    elif isinstance(o, list):
        if not o:
            out[prefix + "[]"] = []
        else:
            for i, v in enumerate(o):
                out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = o
    return out


# ------------------------------ Emporia ------------------------------
def discover_emporia():
    """Devuelve {alias: dict_completo} con todo lo que pyemvue expone por dispositivo."""
    from pyemvue import PyEmVue
    from pyemvue.enums import Scale, Unit

    vue = PyEmVue()
    vue.login(username=EMPORIA_EMAIL, password=EMPORIA_PASSWORD, token_storage_file=TOKEN_FILE)

    devices = vue.get_devices()
    gids = [d.device_gid for d in devices]
    now = datetime.datetime.now(datetime.timezone.utc)
    retry = dict(max_retry_attempts=2, initial_retry_delay=0.5, max_retry_delay=2.0)
    usage_s = vue.get_device_list_usage(gids, now, scale=Scale.SECOND.value, unit=Unit.KWH.value, **retry)
    usage_d = vue.get_device_list_usage(gids, now, scale=Scale.DAY.value, unit=Unit.KWH.value, **retry)
    outlets = {o.device_gid: o for o in vue.get_outlets()}

    out = {}
    for d in devices:
        cfg = EMPORIA_DEVICES.get(d.device_gid)
        if not cfg:
            continue
        try:
            vue.populate_device_properties(d)  # enriquece con ubicación/propiedades
        except Exception:
            pass
        out[cfg["alias"]] = {
            "_source": "pyemvue: get_devices() + get_device_list_usage() + get_outlets()",
            "device": to_dict(d),
            "realtime_usage_1S": to_dict(usage_s.get(d.device_gid)),
            "day_usage_1D": to_dict(usage_d.get(d.device_gid)),
            "outlet_fresh": to_dict(outlets.get(d.device_gid)),
        }
    return out


# ------------------------------ Shelly -------------------------------
def _shelly_post(path):
    base = SHELLY_SERVER if (SHELLY_SERVER or "").startswith("http") else f"https://{SHELLY_SERVER}"
    data = urllib.parse.urlencode({"auth_key": SHELLY_AUTH_KEY}).encode()
    req = urllib.request.Request(
        base.rstrip("/") + path, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def discover_shelly():
    """Devuelve {alias: status_crudo} desde /device/all_status (sin filtrar)."""
    payload = _shelly_post("/device/all_status")
    if not payload.get("isok"):
        raise RuntimeError(f"respuesta no OK: {json.dumps(payload)[:200]}")
    ds = (payload.get("data") or {}).get("devices_status") or {}
    out = {}
    for dev_id, status in ds.items():
        cfg = SHELLY_DEVICES.get(dev_id)
        if not cfg:
            continue
        out[cfg["alias"]] = status  # ya es JSON crudo de la API
    return out


# ------------------------------ salida -------------------------------
def print_jsons(title, data):
    for alias, obj in data.items():
        console.rule(f"[bold]{title} · {alias}[/bold]")
        try:
            console.print_json(json.dumps(obj, default=str, ensure_ascii=False))
        except Exception:
            console.print(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


def comparative_table(emp_sample, she_sample):
    emp_flat = flatten(emp_sample) if emp_sample else {}
    she_flat = flatten(she_sample) if she_sample else {}
    paths = sorted(set(emp_flat) | set(she_flat))

    def ex(v):
        s = json.dumps(v, default=str, ensure_ascii=False) if not isinstance(v, str) else v
        return (s[:40] + "…") if len(s) > 40 else s

    t = Table(show_header=True, header_style="bold", show_lines=False)
    t.add_column("Campo", overflow="fold", max_width=46)
    t.add_column("Emp", justify="center")
    t.add_column("Shelly", justify="center")
    t.add_column("Tipo")
    t.add_column("Ejemplo", overflow="fold", max_width=42)
    for p in paths:
        in_e, in_s = p in emp_flat, p in she_flat
        val = emp_flat.get(p) if in_e else she_flat.get(p)
        typ = type(val).__name__
        t.add_row(
            p,
            "[green]✓[/green]" if in_e else "[dim]✗[/dim]",
            "[green]✓[/green]" if in_s else "[dim]✗[/dim]",
            typ, ex(val),
        )
    console.rule("[bold]Tabla comparativa de campos (todos los posibles)[/bold]")
    console.print(t)
    console.print(
        f"[dim]Emporia: {len(emp_flat)} campos · Shelly: {len(she_flat)} campos · "
        f"unión: {len(paths)} · comunes (mismo nombre): {len(set(emp_flat)&set(she_flat))}[/dim]"
    )


def main():
    console.rule("[bold]Discovery de campos de API — Emporia vs Shelly[/bold]")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    emp_data, she_data = {}, {}

    if EMPORIA_EMAIL and EMPORIA_PASSWORD:
        try:
            emp_data = discover_emporia()
        except Exception as e:
            console.print(f"[red]Error Emporia:[/red] {e}")
    else:
        console.print("[yellow]Faltan credenciales Emporia; se omite.[/yellow]")

    if SHELLY_SERVER and SHELLY_AUTH_KEY:
        try:
            she_data = discover_shelly()
        except Exception as e:
            console.print(f"[red]Error Shelly:[/red] {e}")
    else:
        console.print("[yellow]Faltan credenciales Shelly; se omite.[/yellow]")

    # 3) JSON completo por dispositivo
    print_jsons("EMPORIA", emp_data)
    print_jsons("SHELLY", she_data)

    # Guardar muestras crudas (un device por tecnología)
    if emp_data:
        sample = next(iter(emp_data.values()))
        (OUT_DIR / "discovery_emporia_sample.json").write_text(
            json.dumps(sample, indent=2, default=str, ensure_ascii=False))
    if she_data:
        sample = next(iter(she_data.values()))
        (OUT_DIR / "discovery_shelly_sample.json").write_text(
            json.dumps(sample, indent=2, default=str, ensure_ascii=False))

    # 4) Tabla comparativa
    emp_sample = next(iter(emp_data.values()), None)
    she_sample = next(iter(she_data.values()), None)
    comparative_table(emp_sample, she_sample)

    console.print(f"\n[dim]Muestras guardadas en:[/dim] {OUT_DIR}/discovery_emporia_sample.json · discovery_shelly_sample.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
