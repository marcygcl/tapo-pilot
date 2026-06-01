# Smart Plug Pilot — Bogotá Schools Air Filter Monitoring

Repositorio para el sistema de monitoreo de filtros HEPA via smart plugs TP-Link Tapo P115.  
Piloto 2026 — Colegios públicos de Bogotá.

## Estructura del proyecto

El piloto soporta **múltiples tecnologías de smart plug en paralelo**. Cada
tecnología tiene su propio *collector* y su carpeta de logs, pero todos comparten
el mismo esquema y el mismo dashboard comparativo.

```
tapo-pilot/
├── collectors/
│   ├── common.py             # Config, .env, horario escolar y rutas de logs compartidas
│   ├── tapo/collect.py       # Collector Tapo (TP-Link P115, API local)
│   ├── emporia/collect.py    # Collector Emporia (Vue / smart outlets, pyemvue cloud)
│   └── shelly/collect.py     # Collector Shelly (placeholder)
├── energy_logs/
│   ├── tapo/                 # CSVs diarios de Tapo
│   ├── emporia/              # CSVs diarios de Emporia
│   └── shelly/               # CSVs diarios de Shelly
├── dashboard/
│   └── index.html            # Dashboard comparativo (tabs por tecnología)
├── scripts/
│   ├── utils.py              # Funciones compartidas (legacy)
│   ├── test_tapo_cloud.py    # Prueba de la API cloud de TP-Link
│   └── test_emporia.py       # Prueba de login y listado de dispositivos Emporia
├── docs/
│   └── SETUP.md              # Guía de configuración paso a paso
├── tests/
│   └── test_connection.py    # Verificar conexión a cada plug Tapo
├── config.json               # Colegios, plugs Tapo y dispositivos Emporia (GIDs)
├── .env.example              # Variables de entorno (copiar a .env)
├── .gitignore
└── pyproject.toml / uv.lock  # Dependencias (gestionadas con uv)
```

Cada collector escribe un CSV diario en `energy_logs/<tecnología>/YYYY-MM-DD.csv`
con el esquema común `timestamp, alias, is_on, watts, today_wh, school_hours`
(el de Tapo añade además `aula, colegio, intensity, month_wh, runtime_today_min`).

## Quickstart

### 1. Instalar dependencias

```bash
uv sync
```

### 2. Configurar credenciales

```bash
cp .env.example .env
# Editar .env con las credenciales de cada tecnología:
#   TAPO_EMAIL / TAPO_PASSWORD        (app Tapo)
#   EMPORIA_EMAIL / EMPORIA_PASSWORD  (app Emporia)
```

### 3. Verificar conexión

```bash
uv run python tests/test_connection.py        # plugs Tapo
uv run python scripts/test_emporia.py          # dispositivos Emporia
```

### 4. Iniciar recolección de datos

Cada collector corre por separado. Sin argumentos hace polling cada 5 minutos;
con `--once` toma una sola lectura y sale (es lo que usa GitHub Actions).

```bash
uv run python collectors/tapo/collect.py        # loop continuo
uv run python collectors/emporia/collect.py      # loop continuo
uv run python collectors/emporia/collect.py --once   # una lectura
```

Los datos se guardan en `energy_logs/<tecnología>/YYYY-MM-DD.csv`.

### 5. Dashboard

Abre `dashboard/index.html` (o la versión publicada en GitHub Pages). Muestra una
pestaña **Comparativo** entre tecnologías y una pestaña por cada collector
(Tapo / Emporia / Shelly).

## Dispositivos del piloto

| ID  | Aula | Colegio | IP estática | Estado |
|-----|------|---------|-------------|--------|
| P1  | —    | —       | —           | por configurar |
| P2  | —    | —       | —           | por configurar |
| P3  | —    | —       | —           | por configurar |
| P4  | —    | —       | —           | por configurar |
| P5  | —    | —       | —           | por configurar |
| P6  | —    | —       | —           | por configurar |

> Completar esta tabla después de correr `setup_plugs.py`

## Lógica de banderas

Un aula recibe una bandera si el filtro estuvo **apagado más de 1 día escolar completo** en la semana (lunes–viernes, 7am–4pm).

Las banderas generan una visita programada en lugar de la ronda mensual fija.

## Contexto

- Dispositivo: TP-Link Tapo P115
- Filtro: Smart Air HEPA Sqair (6–38W según intensidad)
- Red requerida: Wi-Fi 2.4GHz
- Datos: timestamp, estado on/off, watts actuales, Wh día, Wh mes, minutos activos
