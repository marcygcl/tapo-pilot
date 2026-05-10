# Guía de Configuración — macOS

## Paso 1: Clonar el repositorio

```bash
git clone https://github.com/TU_USUARIO/tapo-pilot.git
cd tapo-pilot
```

## Paso 2: Instalar dependencias Python

```bash
pip install -r requirements.txt
```

Si tienes múltiples versiones de Python:
```bash
python3 -m pip install -r requirements.txt
```

## Paso 3: Configurar credenciales

```bash
cp .env.example .env
```

Editar `.env` con tu email y contraseña de la app Tapo (los mismos con los que creaste la cuenta TP-Link):

```
TAPO_EMAIL=tu_email@ejemplo.com
TAPO_PASSWORD=tu_contraseña
```

> ⚠️ El archivo `.env` está en `.gitignore` y nunca se sube al repositorio.

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
python tests/test_connection.py
```

Deberías ver todos los plugs en verde con su estado actual.

## Paso 7: Iniciar recolección

```bash
python scripts/collect.py
```

Los datos se guardan en `energy_logs/YYYY-MM-DD.csv`. Para dejar el script corriendo en segundo plano en macOS:

```bash
# En una terminal separada o usando nohup:
nohup python scripts/collect.py > logs/collect.log 2>&1 &

# Ver el proceso corriendo:
ps aux | grep collect.py

# Detenerlo:
kill <PID>
```

## Paso 8: Revisar banderas semanalmente

```bash
python scripts/weekly_flags.py
```

Genera el reporte de banderas de la semana actual. Para una semana específica:

```bash
python scripts/weekly_flags.py 2026-05-05
```

---

## Solución de problemas frecuentes

| Problema | Causa probable | Solución |
|----------|---------------|----------|
| `AuthenticationException` | Email/password incorrectos | Verificar `.env` |
| `Connection refused` | IP incorrecta o plug apagado | Verificar IP en router |
| `Timeout` | Plug fuera de la red Wi-Fi | Asegurarse de estar en 2.4GHz |
| Watts siempre 0 | Plug sin dispositivo conectado | Normal si no hay filtro conectado aún |
| IP cambia entre sesiones | Sin IP estática | Configurar reserva DHCP en el router |
