"""
test_connection.py — Verifica la conexión a todos los plugs registrados en DEVICES.

Uso:
    python tests/test_connection.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from rich.console import Console
from rich.table import Table
from utils import TAPO_EMAIL, TAPO_PASSWORD, DEVICES, classify_intensity

console = Console()


async def test_plug(alias: str, cfg: dict) -> dict:
    try:
        from tapo import ApiClient
        client = ApiClient(TAPO_EMAIL, TAPO_PASSWORD)
        device = await client.p115(cfg["ip"])
        info   = await device.get_device_info()
        usage  = await device.get_energy_usage()
        watts  = round(usage.current_power / 1000, 1)
        return {
            "alias":   alias,
            "aula":    cfg["aula"],
            "colegio": cfg["colegio"],
            "ip":      cfg["ip"],
            "status":  "ok",
            "on":      info.device_on,
            "watts":   watts,
            "intensity": classify_intensity(watts),
        }
    except Exception as e:
        return {
            "alias":   alias,
            "aula":    cfg["aula"],
            "colegio": cfg["colegio"],
            "ip":      cfg["ip"],
            "status":  "error",
            "error":   str(e),
        }


async def main():
    console.rule("[bold]Test de Conexión — Todos los Plugs[/bold]")

    if not DEVICES:
        console.print(
            "[red]No hay dispositivos en DEVICES.[/red]\n"
            "Corre primero: [bold]python scripts/setup_plugs.py[/bold]"
        )
        sys.exit(1)

    console.print(f"Probando {len(DEVICES)} dispositivos...\n")

    tasks   = [test_plug(a, c) for a, c in DEVICES.items()]
    results = await asyncio.gather(*tasks)

    table = Table(show_header=True, header_style="bold")
    table.add_column("Alias")
    table.add_column("Aula")
    table.add_column("Colegio")
    table.add_column("IP")
    table.add_column("Conexión")
    table.add_column("Estado")
    table.add_column("Watts", justify="right")

    ok_count  = 0
    err_count = 0

    for r in results:
        if r["status"] == "ok":
            ok_count += 1
            conn    = "[green]✓ OK[/green]"
            state   = "[green]ON[/green]" if r["on"] else "[red]OFF[/red]"
            watts   = f"{r['watts']}W ({r['intensity']})"
        else:
            err_count += 1
            conn  = "[red]✗ ERROR[/red]"
            state = f"[red]{r.get('error', '?')[:40]}[/red]"
            watts = "—"

        table.add_row(r["alias"], r["aula"], r["colegio"], r["ip"], conn, state, watts)

    console.print(table)
    console.print(f"\n[green]OK: {ok_count}[/green]  [red]Errores: {err_count}[/red]  de {len(DEVICES)} total")

    if err_count > 0:
        console.print(
            "\n[yellow]Tips para errores de conexión:[/yellow]\n"
            "  • Verifica que el plug esté encendido y en la red 2.4GHz\n"
            "  • Comprueba que la IP en utils.py DEVICES coincide con la del router\n"
            "  • Asigna IP estática en el router para evitar cambios\n"
            "  • Verifica TAPO_EMAIL y TAPO_PASSWORD en .env\n"
        )


if __name__ == "__main__":
    asyncio.run(main())
