"""Collector Shelly — PLACEHOLDER.

Aún no implementado. La idea es leer on/off y watts de los enchufes Shelly
(p.ej. Shelly Plug S vía su HTTP API local /rpc Switch.GetStatus, o el cloud de
Shelly) y guardar en energy_logs/shelly/YYYY-MM-DD.csv con el mismo esquema que
los demás collectors:

    timestamp, alias, is_on, watts, today_wh, school_hours

Mantén la misma interfaz que los otros collectors:
    uv run python collectors/shelly/collect.py --once

Para implementarlo:
  1. Agrega una sección "shelly" en config.json con los dispositivos (alias + ip/id).
  2. Reutiliza common.py: log_path("shelly"), is_school_hours(), now_bogota().
  3. Implementa read_all() y reusa el patrón append_csv()/poll_once() de emporia.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # collectors/ -> common

COLLECTOR = "shelly"
CSV_FIELDS = ["timestamp", "alias", "is_on", "watts", "today_wh", "school_hours"]


def main(once=False):
    print("Collector Shelly: aún no implementado (placeholder).")
    print("Esquema CSV previsto:", ", ".join(CSV_FIELDS))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collector Shelly (placeholder)")
    parser.add_argument("--once", action="store_true", help="Una sola lectura y salir (CI)")
    args = parser.parse_args()
    sys.exit(main(once=args.once))
