# Smart Plug Pilot — Bogotá Schools Air Filter Monitoring

Repositorio para el sistema de monitoreo de filtros HEPA via smart plugs TP-Link Tapo P115.  
Piloto 2026 — Colegios públicos de Bogotá.

## Estructura del proyecto

```
tapo-pilot/
├── scripts/
│   ├── setup_plugs.py        # Nombrar y verificar los 6 plugs iniciales
│   ├── collect.py            # Loop de recolección de datos cada 5 min
│   ├── weekly_flags.py       # Detección de aulas con filtro apagado >1 día
│   └── utils.py              # Funciones compartidas
├── energy_logs/              # CSVs diarios generados automáticamente
├── dashboard/
│   └── dashboard.html        # Dashboard de visualización (abrir en browser)
├── docs/
│   └── SETUP.md              # Guía de configuración paso a paso
├── tests/
│   └── test_connection.py    # Verificar conexión a cada plug
├── .env.example              # Variables de entorno (copiar a .env)
├── .gitignore
└── requirements.txt
```

## Quickstart

### 1. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 2. Configurar credenciales

```bash
cp .env.example .env
# Editar .env con tu email y contraseña de la app Tapo
```

### 3. Descubrir y nombrar los plugs

```bash
python scripts/setup_plugs.py
```

Esto detecta todos los P115 en la red, muestra su IP y potencia actual,
y te guía para asignar nombre (aula) y ubicación (colegio) a cada uno.

### 4. Verificar conexión

```bash
python tests/test_connection.py
```

### 5. Iniciar recolección de datos

```bash
python scripts/collect.py
```

Los datos se guardan en `energy_logs/YYYY-MM-DD.csv` con una lectura cada 5 minutos.

### 6. Detección de banderas (correr semanalmente)

```bash
python scripts/weekly_flags.py
```

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
