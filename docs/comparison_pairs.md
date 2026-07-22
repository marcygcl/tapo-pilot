# Pares de comparación PurpleAir (con filtro vs sin filtro)

El Tab 1 ("Experimento") de `dashboard/purpleair.html` compara un **aula con
filtro** contra un **aula de referencia sin filtro** en el mismo colegio. Desde
esta versión el tab es *data-driven*: soporta **varios pares** y el **historial de
dispositivos** de purificador por aula (para comparar, p. ej., Blueair vs Sqair en
la misma aula sin mezclar los dos tramos de datos).

## Modelo de datos (`data/sensor_crosswalk.json`)

Un par son **dos filas** `source:"purpleair"` que comparten `comparison_pair`:

| Campo               | Aula con filtro                    | Aula de referencia        |
|---------------------|------------------------------------|---------------------------|
| `comparison_pair`   | `<school_uid>_<aulaF>_vs_<aulaR>`  | *(mismo valor)*           |
| `pair_role`         | `"filter"`                         | `"reference"`             |
| `purpleair_id`      | id del sensor con filtro           | id del sensor de referencia |
| `device_history`    | array de períodos (ver abajo)      | `[]`                      |
| `has_active_filter` | **derivado**: ¿hay período abierto?| `false`                   |

`has_active_filter` no se edita a mano: se recalcula como "existe algún período de
`device_history` con `end_date: null`".

### `device_history`

Array ordenado de períodos, uno por dispositivo instalado en esa aula:

```json
"device_history": [
  {
    "device_model": "Blueair 211i Max",
    "cadr_m3h": 410,
    "start_date": "2026-07-22",
    "end_date": "2026-08-05",
    "note": "arranque del par"
  },
  {
    "device_model": "Smart Air Sqair",
    "cadr_m3h": 300,
    "start_date": "2026-08-05",
    "end_date": null,
    "note": "reemplazo para comparar modelos en el mismo punto"
  }
]
```

Convenciones de fecha (todas `YYYY-MM-DD`, hora de pared Bogotá):

- `start_date: null` → el dispositivo ya estaba antes del primer dato disponible.
- `end_date: null` → dispositivo **activo hoy** (sólo uno debería tenerlo).
- Los bordes son **`start` inclusivo / `end` exclusivo**: una lectura del día
  `end_date` cuenta para el período **siguiente**, nunca para dos a la vez.

## Cómo lo usa el frontend

- **Selector de par**: si hay más de un par, aparece un `<select>` arriba del Tab 1
  (con un solo par queda oculto y el tab se ve igual que antes).
- **Etiquetas de serie / intro**: el nombre del modelo sale de `activeDevice(device_history)`,
  nunca hardcodeado. El aula con filtro se rotula `"<aula> (<modelo>)"`.
- **Marca en el gráfico**: cada período con `start_date` no nulo dibuja una línea
  vertical punteada en esa fecha, etiquetada con el modelo que entra.
- **Tabla "por dispositivo de filtro"**: el paired t-test se calcula **una vez por
  período** (cada lectura se asigna al modelo activo en su fecha), más una fila
  "rango completo (mezclado)" marcada con ⚠ cuando la ventana abarca más de un
  dispositivo. Aparece sólo cuando el aula tuvo más de un modelo.
- **Explorer (Tab 2)**: el KPI de aula muestra el modelo activo (`con <modelo>`).

La tabla de **ventanas canónicas precalculadas** (`purpleair_stats.json`) es de UN
par; para otro par se muestra un aviso y hay que correr el stats collector apuntado
a ese par (fuera del alcance de este cambio).

## Agregar un par nuevo

```bash
uv run python scripts/add_comparison_pair.py \
    --school-uid 111001XXXXXX \
    --school-name "COLEGIO EJEMPLO (IED)" \
    --localidad "Kennedy" \
    --lat 4.62 --lon -74.15 --address "CL 1 2 3" \
    --filter-id 300111 --filter-room "Aula 5" \
    --ref-id 300222   --ref-room "Aula 6" \
    --device "Blueair 211i Max" --cadr 410 --device-start 2026-07-22
```

Después:

1. **Activar el polling**: en `collectors/purpleair.py`, el segundo par ya está
   registrado en `_SENSOR_REGISTRY` con dos placeholders `None`. Reemplazá cada
   `None` por el `purpleair_id` real (entero) — con eso el collector empieza a
   pollear/backfillear sus CSV en el próximo arranque. Mientras sean `None` no se
   pollean (no generan llamadas a la API). Son los mismos dos ids que van en el
   crosswalk, así que usá los que pasaste a `add_comparison_pair.py`.
2. **Reemplazo de dispositivo** (Blueair → Sqair en la misma aula): editar el
   `device_history` del aula con filtro — poner `end_date` al Blueair y agregar el
   Sqair con `start_date` = misma fecha y `end_date: null`. No se crea un par nuevo.
