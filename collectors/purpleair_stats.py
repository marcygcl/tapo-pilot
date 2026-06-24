"""Análisis estadístico del efecto del filtro Sqair sobre PM2.5 (PurpleAir).

Compara el aula CON filtro (sensor 308614, "Aula 19") contra el aula SIN filtro
(sensor 308572, referencia) con un **paired t-test pareado por timestamp**:

    Para cada instante en que AMBOS sensores tienen lectura, se toma la diferencia
        d = pm25(con filtro) − pm25(sin filtro)
    y se contrasta H0: media(d) = 0  vs  H1: media(d) ≠ 0.

¿Por qué pareado por timestamp? Los dos sensores se muestrean en el mismo momento,
así que cada par comparte día de la semana, hora del día y condiciones exteriores
(estacionalidad, clima, polución de fondo). Parear por instante CONTROLA esos
factores sin necesidad de un modelo ANCOVA/regresión explícito, y es robusto de
implementar sin dependencias científicas. d<0 ⇒ el aula con filtro tiene menos PM2.5.

Caveat (observacional): son dos aulas distintas; cualquier diferencia entre los
cuartos más allá del filtro (volumen, ocupación, ventilación) queda confundida con
el efecto del filtro. Esto no es un ensayo aleatorizado.

Ventanas horarias analizadas (hora de pared en Bogotá):
    school_hours    L–V 06:30–15:00
    extended_hours  L–V 06:30–17:00
    all_hours       todas
    outside_hours   17:00–06:30 (cualquier día)

Salida: energy_logs/purpleair_stats.json
    { "school_hours": {"t_stat","p_value","mean_diff","cohens_d","n","df","sd","significant"}, ... }

Uso:
    uv run python collectors/purpleair_stats.py
"""

import csv
import datetime
import glob
import json
import math
from pathlib import Path

from rich.console import Console
from rich.table import Table

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "energy_logs" / "purpleair"
OUT_PATH = ROOT / "energy_logs" / "purpleair_stats.json"

SENSOR_FILTER = "308614"   # Aula 19, con filtro
SENSOR_REF = "308572"      # aula sin filtro (referencia)

SCHOOL_START, SCHOOL_END, EXT_END = 390, 900, 1020   # 06:30, 15:00, 17:00 en minutos

console = Console()


# --------------------------- p-valor de Student-t ---------------------------
# CDF de la t vía beta incompleta regularizada (Numerical Recipes), sin scipy.

def _betacf(a, b, x):
    MAXIT, EPS, FPMIN = 200, 3e-12, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < EPS:
            break
    return h


def _betai(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log(1.0 - x))
    bt = math.exp(lbeta)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_two_sided_p(t, df):
    """p-valor a dos colas de un estadístico t con df grados de libertad."""
    if df <= 0:
        return float("nan")
    return _betai(df / 2.0, 0.5, df / (df + t * t))


# ------------------------------- datos / pares -------------------------------

def load_paired():
    """Lee todos los CSV y agrupa por timestamp -> {filter, ref, minute, weekday}."""
    by_ts = {}
    for fp in sorted(glob.glob(str(LOGS_DIR / "*.csv"))):
        with open(fp, newline="") as f:
            for r in csv.DictReader(f):
                ts = r.get("timestamp")
                sid = r.get("sensor_id")
                try:
                    pm = float(r.get("pm25"))
                except (TypeError, ValueError):
                    continue
                if not ts or sid not in (SENSOR_FILTER, SENSOR_REF):
                    continue
                slot = by_ts.get(ts)
                if slot is None:
                    try:
                        dt = datetime.datetime.fromisoformat(ts)
                    except ValueError:
                        continue
                    slot = by_ts[ts] = {
                        "minute": dt.hour * 60 + dt.minute,
                        "weekday": dt.weekday(),  # Mon=0 … Sun=6
                    }
                slot["f" if sid == SENSOR_FILTER else "r"] = pm
    return by_ts


def in_window(name, minute, weekday):
    if name == "school_hours":
        return weekday < 5 and SCHOOL_START <= minute <= SCHOOL_END
    if name == "extended_hours":
        return weekday < 5 and SCHOOL_START <= minute <= EXT_END
    if name == "all_hours":
        return True
    if name == "outside_hours":
        return minute >= EXT_END or minute < SCHOOL_START
    return False


WINDOWS = ["school_hours", "extended_hours", "all_hours", "outside_hours"]


def paired_diffs(by_ts):
    """Diferencias d = filtro − referencia por ventana, sólo en instantes pareados."""
    diffs = {w: [] for w in WINDOWS}
    for slot in by_ts.values():
        if "f" not in slot or "r" not in slot:
            continue
        d = slot["f"] - slot["r"]
        for w in WINDOWS:
            if in_window(w, slot["minute"], slot["weekday"]):
                diffs[w].append(d)
    return diffs


def paired_ttest(d):
    """Estadísticos del paired t-test sobre la lista de diferencias d."""
    n = len(d)
    if n < 2:
        return {"n": n, "t_stat": None, "p_value": None, "mean_diff": None,
                "cohens_d": None, "df": max(n - 1, 0), "sd": None, "significant": None}
    mean = sum(d) / n
    var = sum((x - mean) ** 2 for x in d) / (n - 1)
    sd = math.sqrt(var)
    df = n - 1
    if sd == 0:
        return {"n": n, "t_stat": None, "p_value": None, "mean_diff": round(mean, 4),
                "cohens_d": None, "df": df, "sd": 0.0, "significant": None}
    t = mean / (sd / math.sqrt(n))
    p = t_two_sided_p(t, df)
    return {
        "n": n,
        "t_stat": round(t, 4),
        "p_value": round(p, 6),
        "mean_diff": round(mean, 4),     # filtro − referencia (negativo ⇒ filtro reduce PM2.5)
        "cohens_d": round(mean / sd, 4),  # d_z pareado
        "df": df,
        "sd": round(sd, 4),
        "significant": bool(p < 0.05),
    }


def main():
    console.rule("[bold]PurpleAir — análisis estadístico (paired t-test)[/bold]")
    by_ts = load_paired()
    diffs = paired_diffs(by_ts)

    result = {w: paired_ttest(diffs[w]) for w in WINDOWS}
    result["_meta"] = {
        "method": "paired t-test, d = pm25(filter) - pm25(reference), matched by timestamp; "
                  "pairing controls for day-of-week and hour-of-day. Two-sided p-value.",
        "sensor_filter": SENSOR_FILTER,
        "sensor_reference": SENSOR_REF,
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "caveat": "Observational: two different rooms; room differences beyond the filter are confounded.",
    }

    OUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    t = Table(title="Paired t-test · d = filtro − referencia (PM2.5)")
    t.add_column("Ventana", style="bold")
    t.add_column("n", justify="right")
    t.add_column("media d", justify="right")
    t.add_column("t", justify="right")
    t.add_column("p", justify="right")
    t.add_column("Cohen's d", justify="right")
    t.add_column("Sig.")
    for w in WINDOWS:
        r = result[w]
        if r["t_stat"] is None:
            t.add_row(w, str(r["n"]), "—", "—", "—", "—", "[dim]n/d[/dim]")
            continue
        sig = "[green]p<0.05[/green]" if r["significant"] else "[yellow]ns[/yellow]"
        t.add_row(w, str(r["n"]), f"{r['mean_diff']:.2f}", f"{r['t_stat']:.2f}",
                  f"{r['p_value']:.4f}", f"{r['cohens_d']:.2f}", sig)
    console.print(t)
    console.print(f"[green]Guardado:[/green] {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
