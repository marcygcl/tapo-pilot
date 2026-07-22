#!/usr/bin/env python3
"""Agrega un par de comparación PurpleAir (aula con filtro vs aula sin filtro) al
crosswalk, siguiendo el mismo patrón que el par existente (Francisco Primero).

Un "par" son dos filas source:"purpleair" en data/sensor_crosswalk.json que
comparten `comparison_pair`, distinguidas por `pair_role` ("filter"/"reference").
El modelo de purificador y sus fechas viven en `device_history` del aula con
filtro; NO se hardcodea el modelo en ningún lado. `has_active_filter` es DERIVADO
(¿hay un período con end_date null?), no se pasa a mano.

No inventa datos: todos los identificadores se pasan por CLI. Es idempotente por
`purpleair_id` (si el sensor ya está, actualiza sus campos de par en vez de duplicar).

Ejemplo (par nuevo con el Blueair 211i Max instalado hoy):

    uv run python scripts/add_comparison_pair.py \\
        --school-uid 111001XXXXXX \\
        --school-name "COLEGIO EJEMPLO (IED)" \\
        --localidad "Kennedy" \\
        --lat 4.62 --lon -74.15 --address "CL 1 2 3" \\
        --filter-id 300111 --filter-room "Aula 5" \\
        --ref-id 300222   --ref-room "Aula 6" \\
        --device "Blueair 211i Max" --cadr 410 --device-start 2026-07-22

Cuando reemplacen el Blueair por el Sqair EN LA MISMA aula, no se corre esto:
se edita el device_history del aula con filtro a mano (cerrar el Blueair con
end_date y agregar el Sqair con start_date), o con --close-device/--add-device.
Ver docs/comparison_pairs.md.

Después de agregar el par, activá el polling: en collectors/purpleair.py el
segundo par ya está en _SENSOR_REGISTRY con placeholders None; reemplazá esos
None por los mismos dos purpleair_id para que empiece a pollear/backfillear.
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
XW_PATH = ROOT / "data" / "sensor_crosswalk.json"


def _pair_id(school_uid, filter_room, ref_room):
    slug = lambda s: "".join(c.lower() if c.isalnum() else "" for c in s) or "x"
    return f"{school_uid}_{slug(filter_room)}_vs_{slug(ref_room)}"


def _base_row(source, **kw):
    """Fila con las mismas claves que las filas purpleair existentes (mismo orden)."""
    row = {
        "source": source,
        "sensor_name": kw["sensor_name"],
        "sensor_uid": kw.get("sensor_uid", ""),
        "sensor_uid_ext": kw.get("sensor_uid_ext", ""),
        "school_uid": kw["school_uid"],
        "school_name": kw["school_name"],
        "localidad": kw["localidad"],
        "classroom_token": kw["classroom_token"],
        "classroom_name": kw["classroom_name"],
        "treatment": "Filters & monitors",
        "treatment_master": "Filters and monitors",
        "T_Filter": 0,
        "T_Monitor": 0,
        "T_Both": 1,
        "exterior": False,
        "installed_filter": 1 if kw["role"] == "filter" else 0,
        "installed_monitor": 1,
        "filter_install_date": "",
        "lat": kw["lat"],
        "lon": kw["lon"],
        "address": kw["address"],
        "purpleair_id": kw["purpleair_id"],
        "label": kw["label"],
        "has_active_filter": kw["has_active_filter"],
        "comparison_pair": kw["comparison_pair"],
        "pair_role": kw["role"],
        "device_history": kw["device_history"],
    }
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--school-uid", required=True)
    ap.add_argument("--school-name", required=True)
    ap.add_argument("--localidad", required=True)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--address", default="")
    ap.add_argument("--filter-id", type=int, required=True, help="purpleair_id del aula CON filtro")
    ap.add_argument("--filter-room", required=True, help="nombre del aula con filtro, p.ej. 'Aula 5'")
    ap.add_argument("--filter-token", default=None, help="classroom_token (default: slug del nombre)")
    ap.add_argument("--ref-id", type=int, required=True, help="purpleair_id del aula SIN filtro (referencia)")
    ap.add_argument("--ref-room", required=True, help="nombre del aula de referencia")
    ap.add_argument("--ref-token", default=None)
    ap.add_argument("--device", required=True, help="modelo del purificador instalado hoy, p.ej. 'Blueair 211i Max'")
    ap.add_argument("--cadr", type=int, default=None, help="CADR en m³/h del dispositivo")
    ap.add_argument("--device-start", required=True, help="fecha de instalación YYYY-MM-DD (start_date del período abierto)")
    ap.add_argument("--note", default="", help="nota opcional para el período de device_history")
    args = ap.parse_args()

    tok = lambda s: "".join(c.lower() if c.isalnum() else "" for c in s)
    f_tok = args.filter_token or tok(args.filter_room)
    r_tok = args.ref_token or tok(args.ref_room)
    pair_id = _pair_id(args.school_uid, args.filter_room, args.ref_room)

    device_history = [{
        "device_model": args.device,
        "cadr_m3h": args.cadr,
        "start_date": args.device_start,
        "end_date": None,          # abierto: es el dispositivo activo hoy
        "note": args.note,
    }]

    filter_row = _base_row(
        "purpleair", sensor_name=f"pa-{args.filter_id}", school_uid=args.school_uid,
        school_name=args.school_name, localidad=args.localidad,
        classroom_token=f_tok, classroom_name=args.filter_room,
        lat=args.lat, lon=args.lon, address=args.address, purpleair_id=args.filter_id,
        label=f"{args.filter_room} (con filtro {args.device})", has_active_filter=True,
        comparison_pair=pair_id, role="filter", device_history=device_history,
    )
    ref_row = _base_row(
        "purpleair", sensor_name=f"pa-{args.ref_id}", school_uid=args.school_uid,
        school_name=args.school_name, localidad=args.localidad,
        classroom_token=r_tok, classroom_name=args.ref_room,
        lat=args.lat, lon=args.lon, address=args.address, purpleair_id=args.ref_id,
        label=f"{args.ref_room} (sin filtro, referencia)", has_active_filter=False,
        comparison_pair=pair_id, role="reference", device_history=[],
    )

    doc = json.loads(XW_PATH.read_text(encoding="utf-8"))
    sensors = doc["sensors"]

    def upsert(row):
        for i, s in enumerate(sensors):
            if s.get("source") == "purpleair" and s.get("purpleair_id") == row["purpleair_id"]:
                sensors[i] = row
                return "updated"
        sensors.append(row)
        return "added"

    r1, r2 = upsert(filter_row), upsert(ref_row)
    doc["total_sensors"] = len(sensors)
    doc["by_source"] = {}
    for s in sensors:
        doc["by_source"][s["source"]] = doc["by_source"].get(s["source"], 0) + 1

    # Escribe sin newline final, como el archivo original.
    XW_PATH.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Par '{pair_id}': filtro {r1} ({args.filter_id}), referencia {r2} ({args.ref_id}).")
    print(f"Ahora activá el polling: en collectors/purpleair.py, reemplazá los dos None de "
          f"_SENSOR_REGISTRY por {args.filter_id} (con filtro) y {args.ref_id} (referencia).")


if __name__ == "__main__":
    main()
