# SETUP — Estado actual del repositorio

> Snapshot generado automáticamente por inspección del repo · **2026-06-21** (America/Bogota)
> Proyecto: **Smart Plug Pilot — Bogotá 2026** (monitoreo de filtros HEPA en colegios vía smart plugs)

---

## 1. Estructura de directorios

`tree -L 3` (ignorando `.git`, `node_modules`, `__pycache__`):

```
.
├── README.md
├── alerts_log
│   └── 2026-06-01.csv
├── collectors
│   ├── __init__.py
│   ├── common.py
│   ├── emporia
│   │   └── collect.py
│   ├── shelly
│   │   ├── collect.py
│   │   └── test_shelly.py
│   └── tapo
│       └── collect.py
├── config.json
├── dashboard
│   └── index.html
├── data
│   ├── classroom_metadata.dta        # binario Stata (ignorado por git)
│   └── schools.json                  # fuente de schools_master (217 escuelas)
├── docs
│   └── SETUP.md                      # guía de instalación (distinta a este archivo)
├── energy_logs
│   ├── emporia
│   │   ├── 2026-05-31.csv … 2026-06-21.csv   (22 CSVs diarios)
│   ├── shelly
│   │   ├── 2026-06-21.csv
│   │   └── _energy_baseline.json
│   └── tapo
│       ├── 2026-05-10.csv
│       └── 2026-05-13.csv
├── pyproject.toml
├── requirements.txt
├── scripts
│   ├── test_emporia.py
│   ├── test_tapo_cloud.py
│   └── utils.py
├── tests
│   └── test_connection.py
└── uv.lock

15 directorios, 46 archivos
```

Workflows de GitHub Actions (no listados arriba): `.github/workflows/collect.yml`,
`collect_shelly.yml`, `dashboard.yml`.

---

## 2. config.json (sin secretos)

`config.json` **no contiene secretos** — solo referencia nombres de variables de entorno
(`*_env`). Las credenciales reales viven en `.env` (gitignored). Las claves de nivel superior son:
`pilot`, `schools`, `emporia`, `shelly`, `escalation`, `schools_master`.

A continuación el contenido completo **excepto `schools_master`** (217 escuelas, resumidas en §2.1):

```json
{
  "pilot": {
    "name": "Smart Plug Pilot — Bogotá 2026",
    "timezone": "America/Bogota",
    "school_start": "07:00",
    "school_end": "16:00",
    "flag_threshold_days": 1,
    "poll_interval_seconds": 300
  },
  "schools": [
    { "id": "school_1", "name": "Colombia School 1", "lat": 4.678,  "lng": -74.049, "nudge_type": "tbd",
      "plugs": [ {"tapo_alias": "lamp", "aula": "Aula 101", "floor": 1, "ip": "192.168.0.241"},
                 {"tapo_alias": "tv",   "aula": "Aula 102", "floor": 1, "ip": "192.168.0.232"} ] },
    { "id": "school_2", "name": "Colombia School 2", "lat": 4.6512, "lng": -74.063, "nudge_type": "tbd",
      "plugs": [ {"tapo_alias": "air",    "aula": "Aula 103", "floor": 1, "ip": "192.168.0.144"},
                 {"tapo_alias": "coffee", "aula": "Aula 104", "floor": 1, "ip": "192.168.0.119"} ] },
    { "id": "school_3", "name": "Colombia School 3", "lat": 4.692,  "lng": -74.082, "nudge_type": "tbd",
      "plugs": [ {"tapo_alias": "eco",    "aula": "Aula 201", "floor": 2, "ip": "192.168.0.227"},
                 {"tapo_alias": "fridge", "aula": "Aula 202", "floor": 2, "ip": "192.168.0.237"} ] }
  ],
  "emporia": {
    "account_email_env": "EMPORIA_EMAIL",
    "devices": [
      {"alias": "one",   "gid": 606961, "aula": "Salón con filtro 1", "school_uid": "111001002909"},
      {"alias": "two",   "gid": 606964, "aula": "Salón con filtro 1", "school_uid": "111001010740"},
      {"alias": "three", "gid": 606969, "aula": "Salón con filtro 1", "school_uid": "111001010910"},
      {"alias": "four",  "gid": 606991, "aula": "Salón con filtro 1", "school_uid": "111001012301"}
    ]
  },
  "shelly": {
    "server_env": "SHELLY_SERVER",
    "auth_key_env": "SHELLY_AUTH_KEY",
    "devices": [
      {"alias": "shelly_1", "id": "acebe6f4b44c", "aula": "Salón con filtro 1", "school_uid": "111001012556"},
      {"alias": "shelly_0", "id": "acebe6f599cc", "aula": "Salón con filtro 1", "school_uid": "111001014176"}
    ]
  },
  "escalation": {
    "ladder": [
      {"label": "< 24h",  "min_hours": 0,  "max_hours": 24,   "action": "Monitorear",  "channel": "none"},
      {"label": "24–48h", "min_hours": 24, "max_hours": 48,   "action": "WhatsApp",     "channel": "whatsapp"},
      {"label": "48–72h", "min_hours": 48, "max_hours": 72,   "action": "Llamada IVR",  "channel": "ivr"},
      {"label": "> 72h",  "min_hours": 72, "max_hours": null, "action": "Visita",       "channel": "visit"}
    ]
  }
}
```

> Se omiten los campos `_nota` (comentarios internos) por brevedad.

### 2.1 Resumen de `schools_master` (217 escuelas)

| Métrica | Valor |
|---|---|
| Total escuelas | **217** |
| Localidades | 16 |
| Tratamiento `Only monitors` | 75 |
| Tratamiento `Only filters` | 72 |
| Tratamiento `Filters & monitors` | 70 |
| Con `filter_classrooms > 0` | 72 |
| Con plug asignado | 6 |

Esquema de cada entrada (ejemplo real):

```json
{
  "school_uid": "111001000078",
  "name": "COLEGIO DE CULTURA POPULAR (IED)",
  "localidad": "Puente Aranda",
  "lat": 4.61949, "lng": -74.11233,
  "treatment": "Only monitors",
  "filter_classrooms": 0,
  "total_classrooms": 3,
  "plugs": []
}
```

---

## 3. Plugs configurados

Los 6 plugs activos están asignados a escuelas reales (`schools_master`) vía `school_uid`.
Tanto Emporia como Shelly son **dispositivos cloud** (no usan IP local; el plug Tapo legacy sí usaba IP).

### Emporia (4 dispositivos · API cloud `pyemvue`, identificador = `gid`)

| Alias | gid (device) | Aula | Escuela asignada (school_uid) | Localidad |
|-------|--------------|------|-------------------------------|-----------|
| `one`   | 606961 | Salón con filtro 1 | COLEGIO CARLOS ALBAN HOLGUIN (IED) — `111001002909` | Bosa |
| `two`   | 606964 | Salón con filtro 1 | COLEGIO INSTITUTO TECNICO INDUSTRIAL FRANCISCO JOSE DE CALDAS (IED) — `111001010740` | Engativa |
| `three` | 606969 | Salón con filtro 1 | COLEGIO NACIONAL NICOLAS ESGUERRA (IED) — `111001010910` | Kennedy |
| `four`  | 606991 | Salón con filtro 1 | COLEGIO SAN JOSE SUR ORIENTAL (IED) — `111001012301` | Rafael Uribe Uribe |

- Credenciales: `EMPORIA_EMAIL` / `EMPORIA_PASSWORD` (en `.env`).

### Shelly (2 dispositivos · API cloud `/device/all_status`, identificador = `id`)

| Alias | id (device) | Aula | Escuela asignada (school_uid) | Localidad |
|-------|-------------|------|-------------------------------|-----------|
| `shelly_1` | `acebe6f4b44c` | Salón con filtro 1 | COLEGIO ESPAÑA (IED) — `111001012556` | Puente Aranda |
| `shelly_0` | `acebe6f599cc` | Salón con filtro 1 | COLEGIO SAN CRISTOBAL SUR (IED) — `111001014176` | San Cristobal |

- Modelo detectado: **S4PL-00116US** (Shelly Plug US Gen4). Nombres cloud: "Shelly 1" / "Shelly 0".
- Credenciales: `SHELLY_SERVER` (`https://shelly-265-eu.shelly.cloud`) / `SHELLY_AUTH_KEY` (en `.env`).

### Tapo (legacy · API local, NO corre en CI)

6 plugs P115 definidos en `config.json → schools[].plugs` con IPs locales
(`192.168.0.241`, `.232`, `.144`, `.119`, `.227`, `.237`). Usan la red Wi-Fi local del
colegio, por lo que el collector Tapo solo se ejecuta localmente. Credenciales:
`TAPO_EMAIL` / `TAPO_PASSWORD`.

> ⚠️ `school_uid`, `aula` y la asignación de plugs a escuelas son **placeholder** —
> ajustar a la ubicación física real de cada dispositivo.

---

## 4. Librerías Python instaladas

- **Python requerido:** `>=3.13` · entorno gestionado con **uv** (venv en `/Users/marcy/.venv`).
- **Gestor:** `uv` (no `pip` directo). El `uv.lock` resuelve **42 paquetes**.

### Dependencias directas (`pyproject.toml`)

```
pandas>=3.0.2
pyemvue>=0.18.9
python-dotenv>=1.2.2
python-kasa>=0.10.2
rich>=15.0.0
tplink-cloud-api>=5.2.0
twilio>=9.10.9
```

### Conjunto resuelto completo (`uv.lock`, 42 paquetes)

```
aiohappyeyeballs==2.6.2      aiohttp==3.13.5            aiohttp-retry==2.9.1
aiosignal==1.4.0             asyncclick==8.3.0.7        attrs==26.1.0
boto3==1.43.18               botocore==1.43.18          certifi==2026.5.20
cffi==2.0.0                  charset-normalizer==3.4.7  colorama==0.4.6
cryptography==48.0.0         envs==1.4                  frozenlist==1.8.0
idna==3.17                   jmespath==1.1.0            markdown-it-py==4.2.0
mashumaro==3.22              mdurl==0.1.2               multidict==6.7.1
numpy==2.4.6                 pandas==3.0.3              propcache==0.5.2
pycognito==2024.5.1          pycparser==3.0             pyemvue==0.18.9
pygments==2.20.0             pyjwt==2.13.0              python-dateutil==2.9.0.post0
python-dotenv==1.2.2         python-kasa==0.10.2        requests==2.34.2
rich==15.0.0                 s3transfer==0.18.0         six==1.17.0
tplink-cloud-api==5.2.0      twilio==9.10.9             typing-extensions==4.15.0
tzdata==2026.2               urllib3==2.7.0             yarl==1.24.2
```

> Nota: `uv pip list` global apunta al Python de Homebrew (no al venv del proyecto). La
> lista autoritativa es `uv.lock` (arriba). Verificación: `uv run python -c "import pandas"`
> usa `/Users/marcy/.venv/bin/python3` con `pandas 3.0.3`.

---

## 5. Scripts en `collectors/`

| Archivo | Descripción |
|---------|-------------|
| `collectors/__init__.py` | Marcador de paquete (vacío). |
| `collectors/common.py` | **Módulo compartido.** Carga `.env` + `config.json`; expone credenciales (`EMPORIA_*`, `SHELLY_*`, `TAPO_*`), helpers de tiempo (`now_bogota`, `is_school_hours`), `log_path(collector)`, `classify_intensity`, y diccionarios de dispositivos (`EMPORIA_DEVICES`, `SHELLY_DEVICES`, `TAPO_DEVICES`, `SCHOOLS_BY_ID`). |
| `collectors/emporia/collect.py` | **Collector Emporia** (pyemvue cloud). Lee on/off + watts en tiempo real + `today_wh` de los 4 dispositivos; escribe `energy_logs/emporia/YYYY-MM-DD.csv`. Soporta `--once` (GitHub Actions). |
| `collectors/shelly/collect.py` | **Collector Shelly** (cloud `/device/all_status`). Lee on/off + watts + `today_wh` (delta sobre baseline diario); escribe `energy_logs/shelly/YYYY-MM-DD.csv`. Soporta `--once`. |
| `collectors/shelly/test_shelly.py` | **Script de prueba Shelly.** Llama a `/device/all_status` (+ `/interface/device/list` para nombres) y muestra nombre/id/online/is_on/watts en tabla. No escribe CSV. |
| `collectors/tapo/collect.py` | **Collector Tapo** (TP-Link P115, API local). Igual esquema; escribe `energy_logs/tapo/`. **NO corre en CI** (requiere red local del colegio). Soporta `--once`. |

Ejecución típica: `uv run python collectors/<tech>/collect.py [--once]`.

---

## 6. Estado de `energy_logs/`

Un subdirectorio por tecnología; cada uno con CSVs diarios `YYYY-MM-DD.csv`.

| Subdirectorio | CSVs | Rango de fechas | Extra |
|---------------|------|-----------------|-------|
| `energy_logs/emporia/` | **22** | 2026-05-31 → 2026-06-21 | `.gitkeep` |
| `energy_logs/shelly/`  | **1**  | 2026-06-21 | `_energy_baseline.json`, `.gitkeep` |
| `energy_logs/tapo/`    | **2**  | 2026-05-10, 2026-05-13 | `.gitkeep` |

**Esquemas de columnas:**
- Emporia: `timestamp,alias,is_on,watts,today_wh,school_hours`
- Shelly: `timestamp,alias,aula,colegio,is_on,watts,today_wh,school_hours`
- Tapo: `timestamp,alias,aula,colegio,is_on,watts,intensity,today_wh,month_wh,runtime_today_min,school_hours`

**Últimas lecturas registradas:**

```
# energy_logs/emporia/2026-06-21.csv (cola)
2026-06-21T19:06:25-05:00,two,1,0.0,0.0,0
2026-06-21T19:06:25-05:00,three,1,0.0,0.0,0
2026-06-21T19:06:25-05:00,four,0,0.0,0.0,0

# energy_logs/shelly/2026-06-21.csv (cola)
2026-06-21T16:34:52-05:00,shelly_1,Aula 105,Colombia School 2,1,0.0,0.0,0
2026-06-21T16:34:52-05:00,shelly_0,Aula 106,Colombia School 3,1,0.0,0.0,0

# energy_logs/shelly/_energy_baseline.json
{"date": "2026-06-21", "totals": {"acebe6f4b44c": 177.45, "acebe6f599cc": 1179.386}}
```

> Las filas Shelly actuales muestran `aula`/`colegio` antiguos ("Aula 105" / "Colombia
> School 2") porque se escribieron **antes** de reasignar los Shelly a escuelas reales.
> La próxima corrida del collector escribirá los nombres reales (vía `school_uid`).
> `_energy_baseline.json` guarda el contador de energía acumulada por dispositivo para
> derivar `today_wh` como delta del día.

---

## 7. Errores recientes en logs

**No existen archivos de log en el repositorio** (`find . -name '*.log'` → vacío).

Los collectors **no escriben a archivos de log**: imprimen tablas `rich` a *stdout*. La
salida (incluidos errores/tracebacks) se captura en:

- **GitHub Actions** → pestaña *Actions* → runs de `collect.yml` / `collect_shelly.yml`
  (logs online, no versionados en el repo).
- **Ejecución local** → la terminal donde se corre `uv run python collectors/...`
  (o el archivo que se redirija manualmente, p.ej. `nohup ... > emporia.log 2>&1 &`).

No se detectaron tracebacks ni marcadores de error en el árbol de trabajo. Las últimas
lecturas locales (ver §6) son consistentes y sin errores.

Artefacto relacionado: `collectors/emporia/.emporia_tokens.json` (~4 KB, **gitignored**) —
caché de tokens de pyemvue, no es un log.
