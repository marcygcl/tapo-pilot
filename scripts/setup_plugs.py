"""
setup_plugs.py — Descubre, nombra y registra los 6 Tapo P115 del piloto.

Uso:
    python scripts/setup_plugs.py

Qué hace:
    1. Se conecta a cada plug usando IP ingresada manualmente (más confiable que discovery)
    2. Muestra el estado actual (encendido/watts) para confirmar que es el correcto
    3. Te pide asignarle nombre de aula y colegio
    4. Actualiza DEVICES en utils.py automáticamente
    5. Renombra el dispositivo en la app Tapo con el alias asignado
"""

import asyncio
import re
import sys
from pathlib import Path

# Asegurar que scripts/ esté en el path
sys.path.insert(0, str(Path(__file__).parent))

from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich import print as rprint

from utils import TAPO_EMAIL, TAPO_PASSWORD, classify_intensity

console = Console()

# ---------------------------------------------------------------------------
# Número de plugs del piloto
# ---------------------------------------------------------------------------
NUM_PLUGS = 6


async def probe_plug(ip: str):
    """Intenta conectarse a un plug por IP y retorna su información básica."""
    try:
        from tapo import ApiClient
        client = ApiClient(TAPO_EMAIL, TAPO_PASSWORD)
        device = await client.p115(ip)
        info   = await device.get_device_info()
        usage  = await device.get_energy_usage()
        return {
            "ip":          ip,
            "alias":       info.nickname or info.device_id[:8],
            "on":          info.device_on,
            "watts":       round(usage.current_power / 1000, 1),   # mW → W
            "today_wh":    round(usage.today_energy / 1000, 1),
            "runtime_min": usage.today_runtime,
            "device":      device,
        }
    except Exception as e:
        return {"ip": ip, "error": str(e)}


async def rename_plug(device, new_alias: str):
    """Cambia el alias del dispositivo en la nube de Tapo."""
    try:
        await device.set_alias(new_alias)
        return True
    except Exception:
        return False


async def main():
    console.rule("[bold]Setup de Smart Plugs — Piloto Bogotá 2026[/bold]")

    if not TAPO_EMAIL or not TAPO_PASSWORD:
        console.print("[red]Error:[/red] Falta TAPO_EMAIL o TAPO_PASSWORD en .env")
        sys.exit(1)

    console.print(
        f"\nVamos a configurar [bold]{NUM_PLUGS} plugs[/bold] uno por uno.\n"
        "Tip: enciende cada plug y anota su IP desde tu router antes de empezar.\n"
        "Todos los plugs deben estar en la misma red Wi-Fi 2.4GHz.\n"
    )

    registered: list[dict] = []

    for i in range(1, NUM_PLUGS + 1):
        console.rule(f"Plug {i} de {NUM_PLUGS}")

        ip = Prompt.ask(f"  IP del plug {i}")
        if not ip.strip():
            console.print("  [yellow]Saltado.[/yellow]")
            continue

        console.print(f"  Conectando a [bold]{ip}[/bold]...")
        result = await probe_plug(ip.strip())

        if "error" in result:
            console.print(f"  [red]No se pudo conectar:[/red] {result['error']}")
            if not Confirm.ask("  ¿Continuar con el siguiente plug?"):
                break
            continue

        # Mostrar estado actual para confirmar físicamente
        intensity = classify_intensity(result["watts"])
        status_str = (
            f"[green]ENCENDIDO[/green] — {result['watts']}W ({intensity})"
            if result["on"]
            else "[red]APAGADO[/red] — 0W"
        )
        console.print(f"  Estado actual: {status_str}")
        console.print(f"  Alias actual en app: [italic]{result['alias']}[/italic]")
        console.print(f"  Energía hoy: {result['today_wh']} Wh · Runtime: {result['runtime_min']} min")

        console.print()
        aula    = Prompt.ask("  Nombre del aula (ej: Aula 101)")
        colegio = Prompt.ask("  Nombre del colegio (ej: Col. San Francisco)")
        alias   = Prompt.ask(
            "  Alias corto para este plug",
            default=f"plug_{aula.lower().replace(' ', '_')}"
        )

        # Renombrar en la app
        renamed = await rename_plug(result["device"], alias)
        if renamed:
            console.print(f"  [green]✓[/green] Renombrado en la app Tapo como '{alias}'")
        else:
            console.print(f"  [yellow]⚠[/yellow] No se pudo renombrar en la app (sigue funcionando igual)")

        registered.append({
            "alias":   alias,
            "ip":      ip.strip(),
            "aula":    aula,
            "colegio": colegio,
        })
        console.print(f"  [green]✓ Plug registrado correctamente.[/green]\n")

    if not registered:
        console.print("\n[yellow]No se registró ningún plug. Revisa la conexión e intenta de nuevo.[/yellow]")
        sys.exit(1)

    # ---------------------------------------------------------------------------
    # Mostrar resumen
    # ---------------------------------------------------------------------------
    console.rule("Resumen")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Alias", style="bold")
    table.add_column("IP")
    table.add_column("Aula")
    table.add_column("Colegio")
    for d in registered:
        table.add_row(d["alias"], d["ip"], d["aula"], d["colegio"])
    console.print(table)

    # ---------------------------------------------------------------------------
    # Actualizar utils.py con los dispositivos registrados
    # ---------------------------------------------------------------------------
    utils_path = Path(__file__).parent / "utils.py"
    utils_text = utils_path.read_text()

    devices_block = "DEVICES: dict[str, dict] = {\n"
    for d in registered:
        devices_block += (
            f'    "{d["alias"]}": {{'
            f'"ip": "{d["ip"]}", '
            f'"aula": "{d["aula"]}", '
            f'"colegio": "{d["colegio"]}"}},\n'
        )
    devices_block += "}"

    # Reemplazar el bloque DEVICES en utils.py
    new_text = re.sub(
        r"DEVICES: dict\[str, dict\] = \{[^}]*\}",
        devices_block,
        utils_text,
        flags=re.DOTALL,
    )
    utils_path.write_text(new_text)
    console.print("\n[green]✓ DEVICES actualizado en scripts/utils.py[/green]")

    console.print(
        "\nPróximo paso:\n"
        "  [bold]python tests/test_connection.py[/bold]  → verificar todos los plugs\n"
        "  [bold]python scripts/collect.py[/bold]         → iniciar recolección de datos\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
