import asyncio, csv, os, sys, signal
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from rich.console import Console
from rich.table import Table
from utils import (TAPO_EMAIL, TAPO_PASSWORD, DEVICES, POLL_INTERVAL,
    classify_intensity, now_bogota, is_school_hours, log_path)

console = Console()
running = True
CSV_FIELDS = ["timestamp","alias","aula","colegio","is_on","watts","intensity","today_wh","month_wh","runtime_today_min","school_hours"]

def handle_exit(sig, frame):
    global running
    console.print("\n[yellow]Deteniendo...[/yellow]")
    running = False

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

async def read_plug(alias, cfg):
    try:
        from tapo import ApiClient
        client = ApiClient(TAPO_EMAIL, TAPO_PASSWORD)
        device = await client.p115(cfg["ip"])
        info  = await device.get_device_info()
        usage = await device.get_energy_usage()
        watts = round(usage.current_power / 1000, 1)
        return {
            "timestamp":         now_bogota().isoformat(timespec="seconds"),
            "alias":             alias,
            "aula":              cfg["aula"],
            "colegio":           cfg["colegio"],
            "is_on":             int(info.device_on),
            "watts":             watts,
            "intensity":         classify_intensity(watts),
            "today_wh":          round(usage.today_energy / 1000, 1),
            "month_wh":          round(usage.month_energy / 1000, 1),
            "runtime_today_min": usage.today_runtime,
            "school_hours":      int(is_school_hours()),
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
    t.add_column("Watts", justify="right")
    t.add_column("Wh hoy", justify="right")
    for r in readings:
        if r is None: t.add_row("—","[red]ERROR[/red]","—","—"); continue
        t.add_row(r["aula"], "[green]ON[/green]" if r["is_on"] else "[red]OFF[/red]",
                  str(r["watts"]), str(r["today_wh"]))
    return t

async def poll_once():
    tasks = [read_plug(alias, cfg) for alias, cfg in DEVICES.items()]
    return await asyncio.gather(*tasks)

async def main():
    console.rule("[bold]Smart Plug Collection — Local Mode[/bold]")
    if not TAPO_EMAIL or not TAPO_PASSWORD:
        console.print("[red]Faltan credenciales[/red]"); sys.exit(1)

    console.print(f"Monitoreando {len(DEVICES)} plugs · intervalo {POLL_INTERVAL}s · Ctrl+C para detener\n")
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
