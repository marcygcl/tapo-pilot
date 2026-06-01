"""Prueba de conexión con Emporia (pyemvue).

Hace login con las credenciales del .env, lista todos los dispositivos de la
cuenta y, para cada uno, muestra el estado on/off (cuando aplica) y el consumo
en watts en tiempo real.

Uso:
    uv run python scripts/test_emporia.py

Requiere en el .env:
    EMPORIA_EMAIL=...
    EMPORIA_PASSWORD=...
"""

import datetime
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pyemvue import PyEmVue
from pyemvue.enums import Scale, Unit

# Carga el .env de la raíz del repo (este script vive en scripts/).
load_dotenv(Path(__file__).parent.parent / ".env")

EMAIL = os.getenv("EMPORIA_EMAIL")
PASSWORD = os.getenv("EMPORIA_PASSWORD")

# Cachea los tokens para no re-autenticar en cada corrida.
TOKEN_FILE = str(Path(__file__).parent / ".emporia_tokens.json")


def usage_to_watts(kwh_per_second: float) -> float:
    """Convierte el consumo de la escala 1S (kWh en ese segundo) a watts.

    1 kWh consumido en 1 s equivale a 3600 kW de potencia media (hay 3600 s
    en una hora); x1000 -> watts.
    """
    return (kwh_per_second or 0.0) * 3600 * 1000


def main() -> int:
    if not EMAIL or not PASSWORD:
        print("ERROR: faltan EMPORIA_EMAIL / EMPORIA_PASSWORD en el .env")
        print("       Agrégalos (ver .env.example) y vuelve a correr el script.")
        return 1

    vue = PyEmVue()

    print(f"Autenticando como {EMAIL} ...")
    try:
        ok = vue.login(
            username=EMAIL,
            password=PASSWORD,
            token_storage_file=TOKEN_FILE,
        )
    except Exception as e:  # noqa: BLE001 - queremos un mensaje claro, no traceback
        print(f"ERROR de login: {e}")
        return 1

    if not ok:
        print("ERROR: login rechazado (credenciales inválidas).")
        return 1
    print("Login OK\n")

    devices = vue.get_devices()
    print(f"Dispositivos encontrados: {len(devices)}\n")
    if not devices:
        return 0

    # Estado on/off fresco de los outlets (smart plugs), indexado por gid.
    outlet_state = {}
    try:
        for outlet in vue.get_outlets():
            outlet_state[outlet.device_gid] = outlet.outlet_on
    except Exception as e:  # noqa: BLE001
        print(f"(aviso: no se pudo leer el estado de outlets: {e})\n")

    # Consumo en tiempo real de todos los dispositivos en una sola llamada.
    gids = [d.device_gid for d in devices]
    instant = datetime.datetime.now(datetime.timezone.utc)
    usage = vue.get_device_list_usage(
        deviceGids=gids,
        instant=instant,
        scale=Scale.SECOND.value,
        unit=Unit.KWH.value,
    )

    for d in devices:
        name = d.device_name or d.display_name or f"gid {d.device_gid}"
        print("====================")
        print(f"DISPOSITIVO: {name}")
        print(f"  gid:     {d.device_gid}")
        print(f"  modelo:  {d.model}")
        print(f"  online:  {d.connected}")

        # Estado on/off: solo aplica a outlets / cargadores.
        if d.device_gid in outlet_state:
            state = "ON" if outlet_state[d.device_gid] else "OFF"
        elif d.outlet is not None:
            state = "ON" if d.outlet.outlet_on else "OFF"
        elif d.ev_charger is not None:
            state = "ON" if d.ev_charger.charger_on else "OFF"
        else:
            state = "N/A (monitor, sin relé)"
        print(f"  estado:  {state}")

        # Consumo en watts por canal.
        usage_device = usage.get(d.device_gid)
        if not usage_device or not usage_device.channels:
            print("  consumo: sin datos")
            print()
            continue

        total_watts = None
        for channel_num, channel in usage_device.channels.items():
            watts = usage_to_watts(channel.usage)
            label = channel.name or f"canal {channel_num}"
            print(f"  consumo [{channel_num}] {label}: {watts:.1f} W")
            # El canal "1,2,3" es el total de la red (Vue). Para outlets es "1".
            if channel_num == "1,2,3":
                total_watts = watts
        if total_watts is not None:
            print(f"  consumo TOTAL: {total_watts:.1f} W")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
