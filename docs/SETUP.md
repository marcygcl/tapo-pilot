# Guía de Configuración — macOS

## Paso 1: Clonar el repositorio

```bash
git clone https://github.com/TU_USUARIO/tapo-pilot.git
cd tapo-pilot
```

## Paso 2: Instalar dependencias Python

El proyecto usa [uv](https://docs.astral.sh/uv/) para gestionar el entorno y las dependencias:

```bash
uv sync
```

Esto crea el entorno virtual y lo deja listo. Cada comando se corre con `uv run python ...`.

## Paso 3: Configurar credenciales

```bash
cp .env.example .env
```

Editar `.env` con las credenciales de cada tecnología de smart plug que vayas a usar
(los mismos email/contraseña con los que creaste cada cuenta):

```
TAPO_EMAIL=tu_email@ejemplo.com
TAPO_PASSWORD=tu_contraseña

EMPORIA_EMAIL=tu_email@ejemplo.com
EMPORIA_PASSWORD=tu_contraseña
```

> ⚠️ El archivo `.env` está en `.gitignore` y nunca se sube al repositorio.
> El `.env.example` (plantilla) solo lleva los nombres de las variables, sin valores.

## Paso 4: Preparar los plugs físicamente

Antes de correr el script:

1. **Instala la app Tapo** (iOS o Android) si no la tienes.
2. **Añade cada plug a la app** usando el flujo normal de la app — esto crea la cuenta del dispositivo en la nube de TP-Link.
3. **Conecta todos los plugs a la red Wi-Fi 2.4GHz** de tu casa (luego en el colegio usarán la red escolar).
4. **Anota la IP de cada plug** desde la app Tapo (Dispositivo → Configuración → Info del dispositivo) o desde la tabla de dispositivos conectados de tu router.

### Asignar IPs estáticas (recomendado)

En tu router, reserva una IP fija para cada plug usando su dirección MAC. Esto evita que la IP cambie y rompa la recolección.

- Entra al panel de tu router (normalmente `192.168.1.1` o `192.168.0.1`)
- Busca "DHCP Reservations" o "Asignación estática"
- Añade una entrada por cada plug con su MAC y la IP deseada

## Paso 5: Nombrar y registrar los plugs

```bash
python scripts/setup_plugs.py
```

El script:
- Te pide la IP de cada plug uno por uno
- Muestra el consumo actual para que confirmes que es el correcto (enciéndelo y mira si cambia el wattage)
- Te pide nombre de aula y colegio
- Actualiza automáticamente `scripts/utils.py` con los datos

## Paso 6: Verificar la conexión

```bash
uv run python tests/test_connection.py        # plugs Tapo
uv run python scripts/test_emporia.py          # dispositivos Emporia
```

Deberías ver todos los dispositivos con su estado actual.

## Paso 7: Iniciar recolección

Cada tecnología tiene su propio collector en `collectors/<tecnología>/collect.py`.
Sin argumentos hace polling continuo; con `--once` toma una sola lectura y sale.

```bash
uv run python collectors/tapo/collect.py        # Tapo, loop continuo
uv run python collectors/emporia/collect.py      # Emporia, loop continuo
```

Los datos se guardan en `energy_logs/<tecnología>/YYYY-MM-DD.csv`. Para dejar un
collector corriendo en segundo plano en macOS:

```bash
# En una terminal separada o usando nohup:
nohup uv run python collectors/emporia/collect.py > emporia.log 2>&1 &

# Ver el proceso corriendo:
ps aux | grep collect.py

# Detenerlo:
kill <PID>
```

En producción, GitHub Actions (`.github/workflows/collect.yml`) corre cada
collector con `--once` cada 5 minutos y commitea los CSVs automáticamente.

## Paso 8: Dashboard

Abre `dashboard/index.html` en el navegador (o la versión publicada en GitHub
Pages). Tiene una pestaña **Comparativo** entre tecnologías y una pestaña por
cada collector (Tapo / Emporia / Shelly), con estado on/off, watts, Wh del día y
uptime de los últimos 7 días.

---

## Solución de problemas frecuentes

| Problema | Causa probable | Solución |
|----------|---------------|----------|
| `AuthenticationException` | Email/password incorrectos | Verificar `.env` |
| `Connection refused` | IP incorrecta o plug apagado | Verificar IP en router |
| `Timeout` | Plug fuera de la red Wi-Fi | Asegurarse de estar en 2.4GHz |
| Watts siempre 0 | Plug sin dispositivo conectado | Normal si no hay filtro conectado aún |
| IP cambia entre sesiones | Sin IP estática | Configurar reserva DHCP en el router |
