import asyncio, csv, os, sys, signal, base64
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from rich.console import Console
from rich.table import Table
from utils import (TAPO_EMAIL, TAPO_PASSWORD, DEVICES, POLL_INTERVAL,
    now_bogota, is_school_hours, log_path)

console = Console()
running = True
CSV_FIELDS = ["timestamp","alias","aula","colegio","is_on","school_hours"]

def handle_exit(sig, frame):
    global running
    console.print("\n[yellow]Deteniendo...[/yellow]")
    running = False

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

async def get_cloud_devices():
    from tplinkcloud import TPLinkDeviceManager
    mgr = TPLinkDeviceManager(TAPO_EMAIL, TAPO_PASSWORD)
    devs = await mgr.get_devices()
    result = {}
    for d in devs:
        alias = d.get_alias()
        try:
            alias = base64.b64decode(alias).decode('utf-8')
        except Exception:
            pass
        result[alias] = d
    return result

async def read_plug(alias, cfg, dev):
    try:
        is_on = await dev.is_on()
        return {
            "timestamp":    now_bogota().isoformat(timespec="seconds"),
            "alias":        alias,
            "aula":         cfg["aula"],
            "colegio":      cfg["colegio"],
            "is_on":        int(is_on),
            "school_hours": int(is_school_hours()),
        }
    except Exception as e:
        console.print(f"  [red]Error {alias}:[/red] {e}")
        return None

def append_csv(row):
    path = log_path()
    exists = os.path.isfile(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not exists: w.writeheader()
        w.writerow(row)

def make_table(readings):
    t = Table(title=f"Lectura — {now_bogota().strftime('%Y-%m-%d %H:%M:%S')} (Bogotá)")
    t.add_column("Aula", style="bold")
    t.add_column("Estado")
    for r in readings:
        if r is None: t.add_row("—", "[red]ERROR[/red]"); continue
        t.add_row(r["aula"], "[green]ON[/green]" if r["is_on"] else "[red]OFF[/red]")
    return t

async def poll_once():
    try:
        devs = await get_cloud_devices()
    except Exception as e:
        console.print(f"[red]Error nube:[/red] {e}")
        return [None] * len(DEVICES)
    tasks = []
    for alias, cfg in DEVICES.items():
        dev = devs.get(alias)
        if dev is None:
            console.print(f"[yellow]'{alias}' no encontrado[/yellow]")
            tasks.append(asyncio.sleep(0))
        else:
            tasks.append(read_plug(alias, cfg, dev))
    return await asyncio.gather(*tasks)

async def main():
    console.rule("[bold]Smart Plug Collection — Cloud Mode[/bold]")
    if not TAPO_EMAIL or not TAPO_PASSWORD:
        console.print("[red]Faltan credenciales[/red]"); sys.exit(1)

    if "--once" in sys.argv:
        console.print(f"Single-shot · {len(DEVICES)} plugs")
        readings = await poll_once()
        for r in readings:
            if r: append_csv(r)
        console.print(make_table(readings))
        saved = sum(1 for r in readings if r)
        console.print(f"[green]✓ {saved}/{len(DEVICES)} guardadas[/green]")
        return

    console.print(f"Monitoreando {len(DEVICES)} plugs · Ctrl+C para detener\n")
    count = 0
    while running:
        count += 1
        console.print(f"[dim]Lectura #{count}...[/dim]")
        readings = await poll_once()
        for r in readings:
            if r: append_csv(r)
        console.print(make_table(readings))
        for _ in range(POLL_INTERVAL):
            if not running: break
            await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
