"""Evidencias: lo que Saltycripto publicó y cómo terminó, verificable de principio a fin.

Qué se guarda (en el mismo SQLite que las decisiones spot, con tablas nuevas; las existentes no se tocan):
  · proyecciones     una emisión real por activo y hora UTC de la proyección «velas de 1 h · horizonte 24 h», exactamente
                     como se publicó (JSON canónico + huella SHA-256). Un disparador impide modificarla después.
  · resultados_proy  el resultado de cada emisión, aparte y una sola vez (verificada o «sin seguimiento» con su motivo).
  · velas_1m         velas de 1 minuto de Kraken del mismo par, con su procedencia (WebSocket, REST o ambas) y nº de operaciones.
  · cobertura_1m     tramos durante los que se sabe todo lo que pasó: canal de velas en vivo activo o velas REST ya cerradas.
                     Un minuto dentro de un tramo y sin operaciones es «sin operaciones»; fuera de todos, es un hueco.
  · volumen_1m, libro     volumen negociado por lado y minuto, y una foto del libro verificado (CRC) cada 5 min, para experimentos.
  · velas_15m        las velas de 1 min de hace más de 30 días, resumidas en velas de 15 min (el detalle sigue mostrándose).
Todas las monedas del catálogo (cripto/catalogo.py) pasan por el mismo flujo.
  · sesiones         latidos del servicio: dicen cuándo estuvo apagado (las emisiones de esas horas no se reconstruyen).

Reglas de la verificación (ver `evaluar_emision`):
  · Ventana: los minutos completos entre la emisión y el vencimiento (24 h después). El minuto en curso al emitir y el
    minuto en curso al vencer se excluyen enteros: así nunca se atribuye al pronóstico una operación anterior a la emisión
    ni posterior al vencimiento (se pierden como mucho 2 minutos de la ventana).
  · Precio de vencimiento: el último precio negociado en Kraken (mismo par) dentro de la ventana, es decir, el cierre de la
    última vela de 1 minuto con operaciones anterior al vencimiento.
  · Toque de un nivel: exige una operación (el máximo o mínimo de una vela de 1 minuto con operaciones) del mismo par.
    Un toque observado no es una entrada simulada: aquí no se simula ninguna operación.
  · Si falta algún minuto de la ventana, el resultado es «Sin seguimiento» con el motivo; mientras el hueco aún pueda
    rellenarse con las velas REST de Kraken (12 h), la emisión queda pendiente."""
import hashlib
import json
import math
import pathlib
import sqlite3
import threading
import zlib
from contextlib import closing

import numpy as np

from . import proyeccion as P
from .catalogo import SIMBOLOS, par
from .decision import RUTA

VERSION_CALCULO = "proy-vol-1h-24h/1"       # proyección publicada (cripto/proyeccion.py, temporalidad 1 h)
VERSION_EVAL = "eval-1m-kraken/1"
VERSION_EVAL_15 = "eval-15m-kraken/1"      # respaldo: con la app abierta un rato al día, la ventana se comprueba en velas de 15 min
VERSION_REF = "ref-hist-30d/1"
VERSION_EXP = "exp-vol-corta/1"
ACTIVOS = SIMBOLOS               # todas las monedas del catálogo pasan por el mismo flujo
MIN = 60_000
HORA = 3_600_000
DIA = 24 * HORA
H_VELAS = 24                     # velas de 1 h del horizonte
FRESCO = 120_000                 # misma edad máxima de la cotización que la decisión spot
MARGEN = 3 * MIN                 # tras vencer, margen para que la última vela quede guardada
Q = 15 * MIN
REST15_ALCANCE = 7 * DIA         # Kraken REST da 720 velas de 15 min (7,5 días): un hueco más antiguo ya no puede rellenarse
REST_ALCANCE = 690 * MIN         # Kraken REST da 720 velas de 1 min: un hueco más antiguo ya no puede rellenarse
GRACIA_WS = 10_000               # una vela de 1 min del WebSocket se da por cerrada 10 s después de terminar su minuto
TRAMOS = (0, .1, .3, .5, .7, .9, 1.0000001)
MUESTRA_MIN = 30                 # por debajo, «muestra pequeña»
MUESTRA_MIN_DIA = 20
RETENER_1M = 30 * DIA            # velas de 1 min que se guardan tal cual; las más antiguas quedan en velas de 15 min
LIBRO_CADA = 5 * MIN             # una foto del libro cada 5 min por activo (antes, cada minuto)
REEVALUAR = 5 * MIN              # una emisión vencida con un hueco aún rellenable se vuelve a mirar cada 5 min

ESQUEMA = """
CREATE TABLE IF NOT EXISTS proyecciones (simbolo TEXT NOT NULL, hora INTEGER NOT NULL, t_emision INTEGER NOT NULL,
  t_vence INTEGER NOT NULL, version TEXT NOT NULL, precio REAL NOT NULL, contenido TEXT NOT NULL, huella TEXT NOT NULL,
  PRIMARY KEY (simbolo, hora));
CREATE TRIGGER IF NOT EXISTS proyecciones_inmutables BEFORE UPDATE ON proyecciones
  BEGIN SELECT RAISE(ABORT, 'una proyeccion emitida no se modifica'); END;
CREATE TABLE IF NOT EXISTS resultados_proy (simbolo TEXT NOT NULL, hora INTEGER NOT NULL, t_eval INTEGER NOT NULL,
  estado TEXT NOT NULL, motivo TEXT, contenido TEXT NOT NULL, PRIMARY KEY (simbolo, hora));
CREATE TRIGGER IF NOT EXISTS resultados_inmutables BEFORE UPDATE ON resultados_proy
  BEGIN SELECT RAISE(ABORT, 'un resultado registrado no se modifica'); END;
CREATE TABLE IF NOT EXISTS omisiones_proy (simbolo TEXT NOT NULL, hora INTEGER NOT NULL, motivo TEXT, t INTEGER,
  PRIMARY KEY (simbolo, hora));
CREATE TABLE IF NOT EXISTS velas_1m (simbolo TEXT NOT NULL, t INTEGER NOT NULL, o REAL, h REAL, l REAL, c REAL, v REAL,
  n INTEGER, fuente TEXT NOT NULL, recibida INTEGER NOT NULL, PRIMARY KEY (simbolo, t));
CREATE TABLE IF NOT EXISTS cobertura_1m (simbolo TEXT NOT NULL, fuente TEXT NOT NULL, desde INTEGER NOT NULL,
  hasta INTEGER NOT NULL, PRIMARY KEY (simbolo, fuente, desde));
CREATE TABLE IF NOT EXISTS volumen_1m (simbolo TEXT NOT NULL, minuto INTEGER NOT NULL, compra_eur REAL, venta_eur REAL,
  n INTEGER, cantidad REAL, completo INTEGER, fuente TEXT, PRIMARY KEY (simbolo, minuto));
CREATE TABLE IF NOT EXISTS sesiones (inicio INTEGER PRIMARY KEY, latido INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS libro (simbolo TEXT NOT NULL, minuto INTEGER NOT NULL, t INTEGER, bid REAL, ask REAL,
  spread_pct REAL, niveles_compra INTEGER, niveles_venta INTEGER, alcance_compra_pct REAL, alcance_venta_pct REAL, compra_01 REAL, venta_01 REAL,
  compra_05 REAL, venta_05 REAL, compra_1 REAL, venta_1 REAL, PRIMARY KEY (simbolo, minuto)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS velas_15m_rest (simbolo TEXT NOT NULL, t INTEGER NOT NULL, o REAL, h REAL, l REAL, c REAL,
  v REAL, n INTEGER, recibida INTEGER, PRIMARY KEY (simbolo, t)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS velas_15m (simbolo TEXT NOT NULL, t INTEGER NOT NULL, o REAL, h REAL, l REAL, c REAL, v REAL,
  n INTEGER, minutos INTEGER, minutos_ops INTEGER, fuente TEXT, PRIMARY KEY (simbolo, t)) WITHOUT ROWID;
"""
LIBRO_COLS = ("t", "bid", "ask", "spread_pct", "niveles_compra", "niveles_venta", "alcance_compra_pct", "alcance_venta_pct",
              "compra_0,1", "venta_0,1", "compra_0,5", "venta_0,5", "compra_1", "venta_1")
BANDAS = ("compra_0,1", "venta_0,1", "compra_0,5", "venta_0,5", "compra_1", "venta_1")


def fila_libro(foto):
    """Columnas de una foto del libro. Las fotos antiguas (sin alcance) sumaban todo el libro de 25 niveles en cada banda
    aunque no llegara a ella: esas bandas no se pueden comprobar y se guardan vacías."""
    f = dict(foto)
    nv = f.pop("niveles", None)
    if isinstance(nv, (list, tuple)) and len(nv) == 2:
        f["niveles_compra"], f["niveles_venta"] = nv
    if "alcance_compra_pct" not in f:
        for k in BANDAS:
            f[k] = None
    return [f.get(k) for k in LIBRO_COLS]


def canonico(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def huella(texto):
    return hashlib.sha256(texto.encode()).hexdigest()


def empaquetar(texto):
    """La emisión se guarda comprimida (zlib, ~4 veces menos); la huella es la del texto canónico sin comprimir."""
    return zlib.compress(texto.encode(), 9)


def desempaquetar(x):
    """Texto canónico de una emisión (las guardadas antes del 18 nov 2026 por la noche están como texto)."""
    return zlib.decompress(bytes(x)).decode() if isinstance(x, (bytes, memoryview)) else x


def piso(t):
    return t - t % MIN


def techo(t):
    return -(-t // MIN) * MIN


def fusionar(tramos):
    out = []
    for d, h in sorted(tramos):
        if out and d <= out[-1][1]:
            out[-1][1] = max(out[-1][1], h)
        else:
            out.append([d, h])
    return [tuple(x) for x in out]


def minutos_sin_cobertura(tramos, m0, m1):
    """Minutos [m, m+1 min) de [m0, m1) que no caen enteros dentro de ningún tramo."""
    faltan, i, ts = [], 0, fusionar(tramos)
    for m in range(m0, m1, MIN):
        while i < len(ts) and ts[i][1] < m + MIN:
            i += 1
        if not (i < len(ts) and ts[i][0] <= m):
            faltan.append(m)
    return faltan


def agrupar(minutos):
    """[m, m+1, m+2, m+7] -> [(m, m+3 min), (m+7, m+8 min)]"""
    out = []
    for m in minutos:
        if out and out[-1][1] == m:
            out[-1][1] = m + MIN
        else:
            out.append([m, m + MIN])
    return [tuple(x) for x in out]


def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    w = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - w), min(1.0, c + w)]


# ---------- lo que se calcula al emitir (solo con información disponible en ese momento) ----------
def _cerradas(rows, ahora):
    return P.limpiar([r for r in (rows or []) if r["t"] + HORA <= ahora])


def referencia(rows, precio, niveles, ahora):
    """Referencia histórica sencilla: los rendimientos y excursiones de 24 h de las velas de 1 h de Kraken de los últimos
    ~30 días cerradas antes de la emisión, tal cual (sin ajustar por la volatilidad de hoy). Es lo que diría alguien que
    solo mira «cuánto se ha movido en un día este mes»."""
    rows = _cerradas(rows, ahora)
    H = H_VELAS
    if len(rows) < H + 100 or not precio:
        return None
    c = np.array([r["c"] for r in rows], float)
    hi = np.array([r["h"] for r in rows], float)
    lo = np.array([r["l"] for r in rows], float)
    lc = np.log(c)
    idx = np.arange(0, len(c) - H)
    r = lc[idx + H] - lc[idx]
    up = np.log(P._ventanas(hi, H, np.max)[idx]) - lc[idx]
    dn = lc[idx] - np.log(P._ventanas(lo, H, np.min)[idx])
    q = np.quantile(r, (.1, .25, .75, .9))
    out = {"version": VERSION_REF, "n": int(len(idx)), "desde": int(rows[0]["t"]), "hasta": int(rows[-1]["t"] + HORA),
           "metodo": (f"Cuantiles 10-25-75-90 % del rendimiento a 24 h y frecuencia de excursiones de 24 h en las {len(c)} "
                      "velas de 1 h de Kraken cerradas antes de la emisión (~30 días), sin ajustar por la volatilidad actual "
                      "ni centrar la mediana. Las ventanas se solapan."),
           "b50": [float(precio * math.exp(q[1])), float(precio * math.exp(q[2]))],
           "b80": [float(precio * math.exp(q[0])), float(precio * math.exp(q[3]))], "niveles": []}
    for g in niveles:
        d = math.log(g["borde"] / precio) if g["lado"] == "sube" else math.log(precio / g["borde"])
        p = 1.0 if d <= 0 else float(np.mean((up if g["lado"] == "sube" else dn) >= d))
        out["niveles"].append({"id": g["id"], "p": p})
    return out


def experimental(rows, precio, calib, niveles, ahora):
    """Variante experimental (no se publica): la misma distribución normalizada que la proyección publicada, pero con la
    volatilidad de memoria corta (EMA de 20 velas de 1 h de Kraken cerradas) en lugar de la mezcla 20/100. Sirve para
    comparar en paralelo, sobre las mismas emisiones, si reaccionar más deprisa a la volatilidad mejora la cobertura."""
    rows = _cerradas(rows, ahora)
    if len(rows) < 60 or not calib or not precio:
        return None
    lc = np.log(np.array([r["c"] for r in rows], float))
    r2 = np.diff(lc, prepend=lc[0]) ** 2
    sig = float(np.sqrt(max(P._ema_robusta(r2, 20)[-1], 1e-14)))
    esc = sig * math.sqrt(calib["H"])
    q = calib["q"]
    out = {"version": VERSION_EXP, "sigma": sig, "sigma_publicada": float(calib["sigma"]), "sd_pct": esc * 100,
           "metodo": "Mismos cuantiles normalizados que la proyección publicada; volatilidad solo de memoria corta "
                     "(EMA 20 velas de 1 h de Kraken cerradas antes de la emisión). No se publica ni cambia la previsión.",
           "b50": [float(precio * math.exp(q[1] * esc)), float(precio * math.exp(q[3] * esc))],
           "b80": [float(precio * math.exp(q[0] * esc)), float(precio * math.exp(q[4] * esc))], "niveles": []}
    for g in niveles:
        d = math.log(g["borde"] / precio) if g["lado"] == "sube" else math.log(precio / g["borde"])
        p = 1.0 if d <= 0 else P._interp(calib["F_up"] if g["lado"] == "sube" else calib["F_dn"], d / esc)
        out["niveles"].append({"id": g["id"], "p": float(p)})
    return out


def construir_emision(s, o, publicado, calib, ahora, procedencia):
    """Emisión de la proyección 1 h · 24 h tal como se publicó. Devuelve (emisión, None) o (None, motivo)."""
    pt = o.get("precio_t") or 0
    if not pt or not 0 <= ahora - pt <= FRESCO:
        return None, "cotización no vigente (más de 120 s)"
    if not publicado or not calib:
        return None, "sin proyección de 1 h (calibración pendiente o histórico insuficiente)"
    precio = publicado.get("centro") or o.get("price")
    if not (isinstance(precio, (int, float)) and math.isfinite(precio) and precio > 0):
        return None, "precio no válido"
    niveles = [{"id": g["id"], "lado": g["lado"], "borde": g["borde"], "zona_media": g.get("precio"), "p": g["p"],
                "proyectada": bool(g.get("proj"))} for g in publicado.get("niveles") or [] if g.get("borde")]
    raw1h = (o.get("_raw") or {}).get("1h")
    em = {"version": VERSION_CALCULO, "simbolo": s, "hora_utc": ahora - ahora % HORA, "t_emision": ahora,
          "t_vence": ahora + DIA, "velas": "1h", "horizonte_h": 24,
          "mercado": {"bolsa": "Kraken", "par": par(s), "fuente_precio": o.get("precio_fuente")},
          "precio": precio, "b50": publicado["b50"], "b80": publicado["b80"], "niveles": niveles,
          "frescura": {"precio_t": pt, "edad_s": round((ahora - pt) / 1000, 1), **(procedencia or {}),
                       "calculo_t": o.get("t"), "calib_ultima_vela": calib.get("t_ultima"),
                       "calib_muestras": calib.get("muestras")},
          "publicado": publicado,
          "referencia": referencia(raw1h, precio, niveles, ahora),
          "experimental": experimental(raw1h, precio, calib, niveles, ahora)}
    return em, None


# ---------- verificación ----------
def _tiene_ops(v):
    n = v.get("n")
    return (n > 0) if n is not None else (v.get("v") or 0) > 0


def _dentro(b, x):
    return bool(b and b[0] <= x <= b[1])


def evaluar_emision(em, velas, tramos, ahora, sesiones=None):
    """Resultado de una emisión, o None si aún no se puede decidir (no ha vencido o un hueco aún puede rellenarse).
    `velas`: {minuto: vela} de Kraken del mismo par; `tramos`: cobertura [(desde, hasta)]."""
    t0, t1 = em["t_emision"], em["t_vence"]
    if ahora < t1 + MARGEN:
        return None
    m0, m1 = techo(t0), piso(t1)
    faltan = minutos_sin_cobertura(tramos, m0, m1)
    base = {"version": VERSION_EVAL, "ventana": [m0, m1], "minutos": (m1 - m0) // MIN,
            "regla": "Minutos completos entre emisión y vencimiento; precio final = último negociado en Kraken (mismo par) "
                     "en la ventana; toque = máximo o mínimo de una vela de 1 min con operaciones."}
    if faltan:
        if ahora - faltan[0] < REST_ALCANCE:
            return None                                   # todavía puede rellenarse con las velas REST de Kraken
        huecos = agrupar(faltan)
        causa = _causa_hueco(huecos, sesiones)
        return dict(base, estado="sin_seguimiento", huecos=huecos, minutos_sin_datos=len(faltan),
                    motivo=f"{causa}: {len(faltan)} min sin datos en {len(huecos)} tramo{'s' if len(huecos) > 1 else ''}")
    ventana = [velas[m] for m in sorted(velas) if m0 <= m < m1]
    ops = [v for v in ventana if _tiene_ops(v)]
    if not ops:
        return dict(base, estado="sin_seguimiento", motivo="sin operaciones en toda la ventana: no hay precio de vencimiento")
    fin = ops[-1]
    pf = float(fin["c"])
    fuentes = {}
    for v in ventana:
        fuentes[v.get("fuente", "?")] = fuentes.get(v.get("fuente", "?"), 0) + 1

    def rangos(x):
        return {"dentro50": _dentro(x.get("b50"), pf), "dentro80": _dentro(x.get("b80"), pf)} if x else None

    niveles = []
    for g in em["niveles"]:
        sube = g["lado"] == "sube"
        toques = [v for v in ops if (v["h"] >= g["borde"] if sube else v["l"] <= g["borde"])]
        extremo = max(v["h"] for v in ops) if sube else min(v["l"] for v in ops)
        niveles.append({"id": g["id"], "lado": g["lado"], "borde": g["borde"], "tocado": bool(toques),
                        "t_toque": toques[0]["t"] if toques else None, "extremo": extremo})
    return dict(base, estado="verificada", motivo=None, precio_final=pf, t_precio_final=fin["t"],
                minutos_con_ops=len(ops), fuentes=fuentes, publicada=rangos(em), referencia=rangos(em.get("referencia")),
                experimental=rangos(em.get("experimental")), niveles=niveles,
                maximo=max(v["h"] for v in ops), minimo=min(v["l"] for v in ops))


def evaluar_emision_15(em, velas15, ahora):
    """Respaldo con velas de 15 min de Kraken REST (incluyen los tramos sin operaciones, así que cubren todo su periodo).
    Ventana: tramos de 15 min completos entre emisión y vencimiento (se pierden como mucho 15 min en cada extremo);
    precio final = cierre del último tramo con operaciones; toque = máximo o mínimo de un tramo con operaciones.
    None si falta algún tramo que aún puede descargarse."""
    t0, t1 = em["t_emision"], em["t_vence"]
    q0, q1 = -(-t0 // Q) * Q, t1 - t1 % Q
    if ahora < t1 + MARGEN:
        return None
    faltan = [q for q in range(q0, q1, Q) if q not in velas15]
    base = {"version": VERSION_EVAL_15, "resolucion": "15m", "ventana": [q0, q1], "minutos": (q1 - q0) // MIN,
            "regla": "Tramos de 15 min completos entre emisión y vencimiento (Kraken REST, mismo par); precio final = cierre "
                     "del último tramo con operaciones; toque = máximo o mínimo de un tramo con operaciones."}
    if faltan:
        if ahora - faltan[0] < REST15_ALCANCE:
            return None
        return dict(base, estado="sin_seguimiento", minutos_sin_datos=len(faltan) * 15,
                    motivo=f"Sin velas de 15 min: {len(faltan)} tramos de la ventana ya no se pueden descargar")
    ops = [velas15[q] for q in range(q0, q1, Q) if _tiene_ops(velas15[q])]
    if not ops:
        return dict(base, estado="sin_seguimiento", motivo="sin operaciones en toda la ventana: no hay precio de vencimiento")
    pf = float(ops[-1]["c"])

    def rangos(x):
        return {"dentro50": _dentro(x.get("b50"), pf), "dentro80": _dentro(x.get("b80"), pf)} if x else None

    niveles = []
    for g in em["niveles"]:
        sube = g["lado"] == "sube"
        toques = [v for v in ops if (v["h"] >= g["borde"] if sube else v["l"] <= g["borde"])]
        niveles.append({"id": g["id"], "lado": g["lado"], "borde": g["borde"], "tocado": bool(toques),
                        "t_toque": toques[0]["t"] if toques else None,
                        "extremo": max(v["h"] for v in ops) if sube else min(v["l"] for v in ops)})
    return dict(base, estado="verificada", motivo=None, precio_final=pf, t_precio_final=ops[-1]["t"] + Q,
                minutos_con_ops=len(ops) * 15, fuentes={"rest15": len(ops)}, publicada=rangos(em),
                referencia=rangos(em.get("referencia")), experimental=rangos(em.get("experimental")), niveles=niveles,
                maximo=max(v["h"] for v in ops), minimo=min(v["l"] for v in ops))


def _causa_hueco(huecos, sesiones):
    if sesiones is None:
        return "Hueco de datos"
    apagado = any(not any(i <= d and h <= lat + 2 * MIN for i, lat in sesiones) for d, h in huecos)
    return "Servicio apagado" if apagado else "Desconexión de la fuente"


# ---------- estadísticas ----------
def diarias(items):
    """Emisiones separadas al menos 24 h (por activo, de la más antigua en adelante): ventanas que no se solapan."""
    out, ult = [], {}
    for x in sorted(items, key=lambda x: x["t_emision"]):
        s = x["simbolo"]
        if s not in ult or x["t_emision"] >= ult[s] + DIA:
            out.append(x)
            ult[s] = x["t_emision"]
    return out


def _variante(em, nombre):
    return em if nombre == "publicada" else em.get(nombre)


def estadisticas(items):
    """items: [{simbolo, t_emision, em, res}] verificadas. Cobertura y anchura de los rangos y calibración de las
    probabilidades de toque, para la proyección publicada, la referencia y la variante experimental."""
    out = {"n": len(items)}
    for nombre in ("publicada", "referencia", "experimental"):
        filas = [(x["em"], _variante(x["em"], nombre), x["res"]) for x in items if _variante(x["em"], nombre)]
        r = {"n": len(filas)}
        for b in ("50", "80"):
            k = sum(1 for _, v, res in filas if (res.get(nombre) or {}).get("dentro" + b))
            r["cob" + b] = k / len(filas) if filas else None
            r["ic" + b] = wilson(k, len(filas))
            r["anch" + b] = (float(np.mean([(v["b" + b][1] - v["b" + b][0]) / em["precio"] * 100 for em, v, _ in filas]))
                             if filas else None)
        pares = []
        for em, v, res in filas:
            pv = {g["id"]: g["p"] for g in v["niveles"]} if nombre != "publicada" else {g["id"]: g["p"] for g in em["niveles"]}
            for g in res.get("niveles") or []:
                if g["id"] in pv and pv[g["id"]] is not None:
                    pares.append((float(pv[g["id"]]), 1.0 if g["tocado"] else 0.0))
        tramos = []
        for a, b in zip(TRAMOS, TRAMOS[1:]):
            sel = [(p, y) for p, y in pares if a <= p < b]
            tramos.append({"de": a, "a": min(b, 1.0), "n": len(sel),
                           "p_media": float(np.mean([p for p, _ in sel])) if sel else None,
                           "frecuencia": float(np.mean([y for _, y in sel])) if sel else None,
                           "ic": wilson(sum(y for _, y in sel), len(sel)) if sel else None})
        r["toques"] = {"n": len(pares), "tramos": tramos,
                       "brier": float(np.mean([(p - y) ** 2 for p, y in pares])) if pares else None}
        out[nombre] = r
    return out


# ---------- historial interno: cuánta evidencia respalda las proyecciones ----------
def historial(items, simbolos, ahora):
    """Validación interna de la proyección 1 h · 24 h con lo ya verificado, por activo y para el conjunto.
    · Solo cuentan las emisiones de la versión de cálculo vigente (las de otras versiones se cuentan aparte: no se mezclan
      resultados de métodos distintos).
    · Por activo se usan las ventanas diarias sin solapar (las 24 horarias de un día miran el mismo movimiento).
    · El conjunto es la media de las coberturas por activo con el MISMO PESO para cada uno, y solo de los activos con
      muestra suficiente: una moneda con más registros no domina el resultado.
    `items`: [{simbolo, hora, t_emision, estado, motivo, em, res}] (ligeros). Devuelve un dict pequeño para la pantalla."""
    por, aportes = {}, {}
    for s in simbolos:
        its = [x for x in items if x["simbolo"] == s]
        vig = [x for x in its if x["em"]["version"] == VERSION_CALCULO]
        ver = [x for x in vig if x["estado"] == "verificada"]
        d = diarias(ver)
        n = len(d)
        k50 = sum(1 for x in d if (x["res"].get("publicada") or {}).get("dentro50"))
        k80 = sum(1 for x in d if (x["res"].get("publicada") or {}).get("dentro80"))
        sin = [x for x in vig if x["estado"] == "sin_seguimiento"]
        h80 = sum(1 for x in ver if (x["res"].get("publicada") or {}).get("dentro80"))
        h50 = sum(1 for x in ver if (x["res"].get("publicada") or {}).get("dentro50"))
        r = {"n_emisiones": len(vig), "n_horarias": len(ver), "n_diarias": n,
             # cobertura con TODAS las horarias: orientativa (24 ventanas de un día miran casi el mismo movimiento)
             "cob80_h": h80 / len(ver) if ver else None, "cob50_h": h50 / len(ver) if ver else None,
             "pendientes": sum(1 for x in vig if x["estado"] == "pendiente"), "sin_seguimiento": len(sin),
             "sin_seguimiento_48h": sum(1 for x in sin if ahora - x["t_emision"] <= 9 * DIA),
             "motivo_sin_seguimiento": sin[-1]["motivo"] if sin else None,
             "otras_versiones": len(its) - len(vig),
             "cob50": k50 / n if n else None, "cob80": k80 / n if n else None,
             "ic50": wilson(k50, n), "ic80": wilson(k80, n)}
        if n < MUESTRA_MIN_DIA or len(ver) < MUESTRA_MIN:
            r["estado"] = "insuficiente"
        elif r["ic80"][0] <= .8 <= r["ic80"][1] and r["ic50"][0] <= .5 <= r["ic50"][1]:
            r["estado"] = "en_linea"
        else:
            bajo = r["ic80"][1] < .8 or r["ic50"][1] < .5
            r["estado"], r["sentido"] = "desviada", ("estrecho" if bajo else "ancho")
        por[s] = r
    suf = [s for s in simbolos if por[s]["estado"] != "insuficiente"]
    glob = {"activos": len(suf), "version": VERSION_CALCULO, "estado": "insuficiente" if not suf else None}
    if suf:
        k = len(suf)
        for b, obj in (("50", .5), ("80", .8)):
            ps = [(por[s]["cob" + b], por[s]["n_diarias"]) for s in suf]
            m = sum(p for p, _ in ps) / k
            se = math.sqrt(sum(p * (1 - p) / n for p, n in ps)) / k
            se = max(se, math.sqrt(m * (1 - m) / sum(n for _, n in ps)) if 0 < m < 1 else 1 / sum(n for _, n in ps))
            glob["cob" + b], glob["ic" + b] = m, [max(0.0, m - 1.96 * se), min(1.0, m + 1.96 * se)]
        aportes = {s: {"peso": 1 / k, "n_diarias": por[s]["n_diarias"], "cob80": por[s]["cob80"]} for s in suf}
        ok = glob["ic80"][0] <= .8 <= glob["ic80"][1] and glob["ic50"][0] <= .5 <= glob["ic50"][1]
        glob["estado"] = "en_linea" if ok else "desviada"
    glob["aportes"] = aportes
    nh = sum(por[s]["n_horarias"] for s in simbolos)
    glob["horarias"] = {"n": nh, "cob80": sum((por[s]["cob80_h"] or 0) * por[s]["n_horarias"] for s in simbolos) / nh if nh else None,
                        "cob50": sum((por[s]["cob50_h"] or 0) * por[s]["n_horarias"] for s in simbolos) / nh if nh else None,
                        "pendientes": sum(por[s]["pendientes"] for s in simbolos),
                        "max_diarias": max((por[s]["n_diarias"] for s in simbolos), default=0)}
    return {"t": ahora, "version": VERSION_CALCULO, "muestra_min_dia": MUESTRA_MIN_DIA, "muestra_min": MUESTRA_MIN,
            "activos": por, "global": glob}


def _var_ligera(x):
    return x and {"version": x.get("version"), "b50": x["b50"], "b80": x["b80"],
                  "niveles": [{"id": g["id"], "p": g["p"]} for g in x.get("niveles") or []]}


def ligera(em):
    """Lo que necesitan las estadísticas y la lista de una emisión (sin lo publicado completo, la frescura ni los métodos)."""
    return {"version": em["version"], "simbolo": em["simbolo"], "hora_utc": em["hora_utc"], "t_emision": em["t_emision"],
            "t_vence": em["t_vence"], "precio": em["precio"], "b50": em["b50"], "b80": em["b80"], "mercado": em["mercado"],
            "niveles": [{"id": g["id"], "lado": g["lado"], "borde": g["borde"], "p": g["p"]} for g in em["niveles"]],
            "referencia": _var_ligera(em.get("referencia")), "experimental": _var_ligera(em.get("experimental"))}


def res_ligero(res):
    if not res:
        return None
    out = {k: res.get(k) for k in ("estado", "motivo", "publicada", "referencia", "experimental")}
    out["niveles"] = [{"id": g["id"], "tocado": g["tocado"]} for g in res.get("niveles") or []]
    return out


# ---------- almacén ----------
class Almacen:
    def __init__(self, ruta=RUTA):
        self.ruta = pathlib.Path(ruta)
        self.lock = threading.Lock()
        self._emitidas = set()
        self._consultadas = set()
        self.sesion = None
        self.error = None
        self.ultima_eval = None
        self._items = {}                 # (símbolo, hora) -> emisión ligera con su resultado (inmutables: se leen una vez)
        self._items_lock = threading.Lock()
        self._revisada = {}              # (símbolo, hora) -> última vez que se intentó evaluar sin poder decidir
        self._libro_t = {}
        self._compactada = 0
        self.cambios = 0                 # sube con cada emisión o resultado nuevo (el historial interno se recalcula entonces)
        with self._db() as db, db:
            db.executescript(ESQUEMA)
            self._migrar_libro(db)
            self._fusionar_cobertura(db)

    @staticmethod
    def _fusionar_cobertura(db):
        """Junta los tramos de cobertura que se solapan (antes cada sincronización REST añadía una fila)."""
        for s, f, n in db.execute("SELECT simbolo, fuente, count(*) FROM cobertura_1m GROUP BY 1, 2").fetchall():
            tr = [(d, h) for d, h in db.execute("SELECT desde, hasta FROM cobertura_1m WHERE simbolo=? AND fuente=?", (s, f))]
            fu = fusionar(tr)
            if len(fu) < n:
                db.execute("DELETE FROM cobertura_1m WHERE simbolo=? AND fuente=?", (s, f))
                db.executemany("INSERT INTO cobertura_1m VALUES (?,?,?,?)", [(s, f, d, h) for d, h in fu])

    @staticmethod
    def _migrar_libro(db):
        """El libro se guardaba como JSON por minuto (libro_1m); ahora en columnas (libro). Se copian las fotos antiguas y
        se elimina la tabla vieja dentro de la misma transacción: no se pierde ninguna."""
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='libro_1m'").fetchone():
            return
        filas = db.execute("SELECT simbolo, minuto, contenido FROM libro_1m").fetchall()
        for s, m, texto in filas:
            try:
                f = json.loads(texto)
            except (TypeError, ValueError):
                continue
            db.execute("INSERT OR IGNORE INTO libro VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (s, m, *fila_libro(f)))
        n = db.execute("SELECT count(*) FROM libro WHERE (simbolo, minuto) IN (SELECT simbolo, minuto FROM libro_1m)").fetchone()[0]
        if n == len(filas):
            db.execute("DROP TABLE libro_1m")

    def _db(self):
        return closing(sqlite3.connect(self.ruta, timeout=5))

    # ----- servicio -----
    def latido(self, ahora):
        with self.lock, self._db() as db, db:
            if self.sesion is None:
                self.sesion = ahora
                db.execute("INSERT OR IGNORE INTO sesiones VALUES (?,?)", (ahora, ahora))
            else:
                db.execute("UPDATE sesiones SET latido=? WHERE inicio=?", (ahora, self.sesion))

    def sesiones(self, desde=0):
        with self._db() as db:
            return [tuple(x) for x in db.execute("SELECT inicio, latido FROM sesiones WHERE latido>=? ORDER BY inicio", (desde,))]

    # ----- emisiones -----
    def emitida(self, s, hora):
        k = (s, hora)
        if k in self._emitidas:
            return True
        if k not in self._consultadas:
            self._consultadas.add(k)
            with self._db() as db:
                if db.execute("SELECT 1 FROM proyecciones WHERE simbolo=? AND hora=?", k).fetchone():
                    self._emitidas.add(k)
                    return True
        return False

    def emitir(self, em):
        """Guarda la emisión tal cual (una por activo y hora UTC). Devuelve True si es nueva."""
        s, hora = em["simbolo"], em["hora_utc"]
        if self.emitida(s, hora):
            return False
        texto = canonico(em)
        with self.lock, self._db() as db, db:
            cur = db.execute("INSERT OR IGNORE INTO proyecciones VALUES (?,?,?,?,?,?,?,?)",
                             (s, hora, em["t_emision"], em["t_vence"], em["version"], em["precio"], empaquetar(texto), huella(texto)))
            if cur.rowcount:             # una omisión anotada antes en esa misma hora (otro arranque) ya no vale
                db.execute("DELETE FROM omisiones_proy WHERE simbolo=? AND hora=?", (s, hora))
        self._emitidas.add((s, hora))
        self.cambios += cur.rowcount
        return cur.rowcount == 1

    def omision(self, s, hora, motivo, ahora):
        with self.lock, self._db() as db, db:
            if not db.execute("SELECT 1 FROM proyecciones WHERE simbolo=? AND hora=?", (s, hora)).fetchone():
                db.execute("INSERT OR REPLACE INTO omisiones_proy VALUES (?,?,?,?)", (s, hora, motivo, ahora))

    # ----- datos de Kraken con procedencia -----
    def guardar_ws(self, s, velas, tramos, limite, ahora):
        """Velas de 1 min cerradas del WebSocket y tramos de cobertura hasta `limite` (todas las velas que terminan antes
        de `limite` ya están en `velas` o en la base, así que el tramo no promete nada que no se haya guardado)."""
        with self.lock, self._db() as db, db:
            for v in velas:
                db.execute("""INSERT INTO velas_1m VALUES (?,?,?,?,?,?,?,?, 'ws', ?)
                              ON CONFLICT(simbolo, t) DO UPDATE SET h=max(h, excluded.h), l=min(l, excluded.l), c=excluded.c,
                              v=max(v, excluded.v), n=max(coalesce(n, 0), coalesce(excluded.n, 0)) WHERE fuente='ws'""",
                           (s, v["t"], v["o"], v["h"], v["l"], v["c"], v["v"], v.get("n"), ahora))
            for d, h in tramos:
                h = min(h, limite)
                if h - d >= MIN:
                    db.execute("""INSERT INTO cobertura_1m VALUES (?, 'ws', ?, ?)
                                  ON CONFLICT(simbolo, fuente, desde) DO UPDATE SET hasta=max(hasta, excluded.hasta)""", (s, d, h))

    def guardar_rest(self, s, filas, ultimo_cerrado, ahora):
        """Velas REST de Kraken (OHLC interval=1). Solo las ya cerradas (t ≤ `last` de Kraken); incluyen los minutos sin
        operaciones (n = 0), así que cubren todo su tramo."""
        cerradas = [r for r in filas if r["t"] <= ultimo_cerrado]
        if not cerradas:
            return 0
        with self.lock, self._db() as db, db:
            # solo lo que aún no vino por REST (antes se reescribían las 720 velas en cada sincronización)
            ya = db.execute("""SELECT max(hasta) FROM cobertura_1m WHERE simbolo=? AND fuente='rest' AND desde<=?""",
                            (s, cerradas[-1]["t"])).fetchone()[0] or 0
            nuevas = [v for v in cerradas if v["t"] + MIN > ya]
            db.executemany("""INSERT INTO velas_1m VALUES (?,?,?,?,?,?,?,?, 'rest', ?)
                              ON CONFLICT(simbolo, t) DO UPDATE SET o=excluded.o, h=excluded.h, l=excluded.l, c=excluded.c,
                              v=excluded.v, n=excluded.n, fuente='ws+rest' WHERE fuente='ws'""",
                           [(s, v["t"], v["o"], v["h"], v["l"], v["c"], v["v"], v.get("n"), ahora) for v in nuevas])
            d, h = cerradas[0]["t"], ultimo_cerrado + MIN
            # el tramo REST nuevo prolonga el que se solapa con él (antes se creaba una fila por sincronización)
            cur = db.execute("""UPDATE cobertura_1m SET hasta=max(hasta, ?), desde=min(desde, ?) WHERE rowid=(SELECT rowid
                                FROM cobertura_1m WHERE simbolo=? AND fuente='rest' AND desde<=? AND hasta>=? ORDER BY hasta DESC LIMIT 1)""",
                             (h, d, s, h, d))
            if not cur.rowcount:
                db.execute("""INSERT INTO cobertura_1m VALUES (?, 'rest', ?, ?)
                              ON CONFLICT(simbolo, fuente, desde) DO UPDATE SET hasta=max(hasta, excluded.hasta)""", (s, d, h))
        return len(nuevas)

    def guardar_rest15(self, s, filas, ultimo_cerrado, ahora):
        """Velas de 15 min de Kraken REST ya cerradas (t ≤ `last`). No cambian una vez cerradas: se insertan una vez."""
        cerradas = [r for r in filas if r["t"] <= ultimo_cerrado]
        with self.lock, self._db() as db, db:
            return db.executemany("INSERT OR IGNORE INTO velas_15m_rest VALUES (?,?,?,?,?,?,?,?,?)",
                                  [(s, v["t"], v["o"], v["h"], v["l"], v["c"], v["v"], v.get("n"), ahora) for v in cerradas]).rowcount

    def velas15(self, s, q0, q1):
        with self._db() as db:
            return {r[0]: {"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5], "n": r[6]}
                    for r in db.execute("SELECT t,o,h,l,c,v,n FROM velas_15m_rest WHERE simbolo=? AND t>=? AND t<? ORDER BY t",
                                        (s, q0, q1))}

    def guardar_volumen(self, s, filas):
        with self.lock, self._db() as db, db:
            db.executemany("INSERT OR IGNORE INTO volumen_1m VALUES (?,?,?,?,?,?,?, 'kraken-ws-operaciones')",
                           [(s, f["minuto"], f["compra"], f["venta"], f["n"], f["q"], int(bool(f["completo"]))) for f in filas])

    def guardar_libro(self, s, foto, ahora):
        """Una foto del libro verificado (CRC) cada LIBRO_CADA por activo, en columnas."""
        m = ahora - ahora % LIBRO_CADA
        if not foto or self._libro_t.get(s) == m:
            return
        with self.lock, self._db() as db, db:
            db.execute("INSERT OR IGNORE INTO libro VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (s, m, *fila_libro(foto)))
        self._libro_t[s] = m

    def velas(self, s, m0, m1):
        with self._db() as db:
            return {r[0]: {"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5], "n": r[6], "fuente": r[7]}
                    for r in db.execute("SELECT t,o,h,l,c,v,n,fuente FROM velas_1m WHERE simbolo=? AND t>=? AND t<? ORDER BY t",
                                        (s, m0, m1))}

    def tramos(self, s, m0, m1):
        with self._db() as db:
            return [(d, h) for d, h in db.execute("SELECT desde, hasta FROM cobertura_1m WHERE simbolo=? AND hasta>? AND desde<?",
                                                  (s, m0, m1))]

    def minutos(self, s, n, ahora):
        """Estado de los últimos n minutos cerrados: con operaciones, sin operaciones (canal activo) o hueco."""
        m1 = piso(ahora)
        m0 = m1 - n * MIN
        v = self.velas(s, m0, m1)
        falta = set(minutos_sin_cobertura(self.tramos(s, m0, m1), m0, m1))
        out = []
        for m in range(m0, m1, MIN):
            x = v.get(m)
            estado = ("ops" if x and _tiene_ops(x) else "hueco" if m in falta and not x else "sin_ops")
            out.append({"t": m, "e": estado, "c": x and x["c"], "n": x and x["n"], "f": x and x["fuente"]})
        return out

    # ----- verificación -----
    def evaluar_pendientes(self, ahora, forzar=False):
        """Evalúa las emisiones vencidas sin resultado. Idempotente: un resultado se escribe una sola vez y, con los mismos
        datos, ejecutarlo de nuevo no cambia nada. Se llama al arrancar (recupera las pendientes) y cada minuto."""
        with self._db() as db:
            pend = db.execute("""SELECT p.simbolo, p.hora, p.contenido FROM proyecciones p LEFT JOIN resultados_proy r
                                 ON r.simbolo=p.simbolo AND r.hora=p.hora WHERE r.hora IS NULL AND p.t_vence<=? ORDER BY p.hora""",
                              (ahora - MARGEN,)).fetchall()
        hechas = 0
        ses = None
        for s, hora, texto in pend:
            if not forzar and ahora - self._revisada.get((s, hora), -REEVALUAR) < REEVALUAR:
                continue                 # con un hueco aún rellenable no se relee la ventana cada 30 s
            ses = ses if ses is not None else self.sesiones()
            em = json.loads(desempaquetar(texto))
            if em["mercado"].get("par") != par(s):
                res = {"version": VERSION_EVAL, "estado": "sin_seguimiento",
                       "motivo": f"Mercado distinto: se emitió para {em['mercado'].get('par')} y ahora se guardan velas de {par(s)}"}
                with self.lock, self._db() as db, db:
                    hechas += db.execute("INSERT OR IGNORE INTO resultados_proy VALUES (?,?,?,?,?,?)",
                                         (s, hora, ahora, res["estado"], res["motivo"], canonico(res))).rowcount
                continue
            m0, m1 = techo(em["t_emision"]), piso(em["t_vence"])
            res = evaluar_emision(em, self.velas(s, m0, m1), self.tramos(s, m0, m1), ahora, ses)
            if res is not None and res["estado"] == "sin_seguimiento" and res.get("huecos"):
                # huecos de 1 min que ya no se pueden rellenar (la app estuvo cerrada): se comprueba con velas de 15 min
                r15 = evaluar_emision_15(em, self.velas15(s, m0 - Q, m1 + Q), ahora)
                if r15 is None:
                    self._revisada[(s, hora)] = ahora
                    continue
                if r15["estado"] == "verificada":
                    res = r15
                else:
                    res = dict(res, motivo=res["motivo"] + " (y " + r15["motivo"][0].lower() + r15["motivo"][1:] + ")")
            if res is None:
                self._revisada[(s, hora)] = ahora
                continue
            self._revisada.pop((s, hora), None)
            with self.lock, self._db() as db, db:
                cur = db.execute("INSERT OR IGNORE INTO resultados_proy VALUES (?,?,?,?,?,?)",
                                 (s, hora, ahora, res["estado"], res.get("motivo"), canonico(res)))
                hechas += cur.rowcount
        self.cambios += hechas
        self.ultima_eval = ahora
        return hechas

    # ----- lectura (pantalla, diagnóstico y validación interna) -----
    def items(self, s="*"):
        """Emisiones ligeras con su resultado. Emisiones y resultados son inmutables: cada uno se lee y se interpreta una
        sola vez (antes, cada consulta volvía a leer y analizar el JSON de todas las emisiones)."""
        with self._db() as db:
            claves = db.execute("""SELECT p.simbolo, p.hora, r.estado FROM proyecciones p LEFT JOIN resultados_proy r
                                   ON r.simbolo=p.simbolo AND r.hora=p.hora ORDER BY p.hora""").fetchall()
            with self._items_lock:
                faltan = [(a, b) for a, b, e in claves if (a, b) not in self._items or self._items[(a, b)]["estado"] != (e or "pendiente")]
                if faltan:
                    q = """SELECT p.simbolo, p.hora, p.contenido, r.estado, r.motivo, r.contenido FROM proyecciones p
                           LEFT JOIN resultados_proy r ON r.simbolo=p.simbolo AND r.hora=p.hora"""
                    filas = (db.execute(q).fetchall() if len(faltan) > 40 else
                             [db.execute(q + " WHERE p.simbolo=? AND p.hora=?", k).fetchone() for k in faltan])
                    pedidas = set(faltan)
                    for a, b, texto, estado, motivo, rtexto in filas:
                        if (a, b) in pedidas:
                            em = json.loads(desempaquetar(texto))
                            self._items[(a, b)] = {"simbolo": a, "hora": b, "t_emision": em["t_emision"], "em": ligera(em),
                                                   "estado": estado or "pendiente", "motivo": motivo,
                                                   "res": res_ligero(json.loads(rtexto)) if rtexto else None}
                out = [self._items[(a, b)] for a, b, _ in claves if s in ("*", a)]
        return out

    def historial(self, ahora, simbolos=ACTIVOS):
        return historial(self.items(), simbolos, ahora)

    def resumen(self, s, ahora, limite=96):
        filas = self.items(s)
        items, lista = [], []
        cuenta = {"pendientes": 0, "verificadas": 0, "sin_seguimiento": 0}
        for x in filas:
            em, res, estado = x["em"], x["res"], x["estado"]
            if estado == "verificada":
                cuenta["verificadas"] += 1
                items.append(x)
            elif estado == "sin_seguimiento":
                cuenta["sin_seguimiento"] += 1
            else:
                cuenta["pendientes"] += 1
            lista.append({"simbolo": x["simbolo"], "hora": x["hora"], "t_emision": em["t_emision"], "t_vence": em["t_vence"],
                          "precio": em["precio"], "b50": em["b50"], "b80": em["b80"], "estado": estado,
                          "motivo": x["motivo"] or (("vence " if ahora < em["t_vence"] else "esperando datos de ") + "Kraken"),
                          "version": em["version"], "fuente": em["mercado"].get("fuente_precio"),
                          "n_niveles": len(em["niveles"]),
                          "dentro50": res and (res.get("publicada") or {}).get("dentro50"),
                          "dentro80": res and (res.get("publicada") or {}).get("dentro80"),
                          "tocados": res and sum(1 for g in res.get("niveles") or [] if g["tocado"])})
        with self._db() as db:
            om = db.execute("SELECT simbolo, hora, motivo FROM omisiones_proy WHERE (?='*' OR simbolo=?) ORDER BY hora",
                            (s, s)).fetchall()
        primera = filas[0]["hora"] if filas else None
        apagado = self._horas_apagado(primera, ahora) if primera else []
        # las estadísticas solo comparan resultados de la misma versión de cálculo
        vig = [x for x in items if x["em"]["version"] == VERSION_CALCULO]
        d = diarias(vig)
        return {"simbolo": s, "ahora": ahora, "cuenta": cuenta, "emisiones": len(filas), "captura": self.captura(s),
                "omitidas": [{"simbolo": a, "hora": b, "motivo": c} for a, b, c in om[-48:]], "n_omitidas": len(om),
                "horas_apagado": len(apagado), "apagado": apagado[-24:], "version": VERSION_CALCULO,
                "otras_versiones": len(items) - len(vig),
                "todas": estadisticas(vig), "diarias": estadisticas(d),
                "muestra_min": MUESTRA_MIN, "muestra_min_dia": MUESTRA_MIN_DIA,
                "lista": lista[::-1][:limite]}

    def captura(self, s):
        """Cuánto se ha capturado para los experimentos (velas por procedencia, volumen por lado, fotos del libro)."""
        with self._db() as db:
            fuentes = dict(db.execute("SELECT fuente, count(*) FROM velas_1m WHERE simbolo=? GROUP BY fuente", (s,)).fetchall())
            r = {"velas": fuentes}
            for t, col in (("velas_1m", "t"), ("velas_15m", "t"), ("volumen_1m", "minuto"), ("libro", "minuto")):
                n, a, b = db.execute(f"SELECT count(*), min({col}), max({col}) FROM {t} WHERE simbolo=?", (s,)).fetchone()
                r[t] = {"n": n, "desde": a, "hasta": b}
            r["volumen_1m"]["completos"] = db.execute("SELECT count(*) FROM volumen_1m WHERE simbolo=? AND completo=1",
                                                      (s,)).fetchone()[0]
        return r

    def compactar(self, ahora, retener=RETENER_1M):
        """Las velas de 1 min de hace más de `retener` pasan a velas de 15 min (máximo, mínimo, último cierre con
        operaciones, volumen y operaciones sumados, minutos cubiertos) y se borran. Los resultados ya están guardados y son
        inmutables; ninguna emisión pendiente puede necesitarlas (un hueco de más de 12 h ya es «sin seguimiento»).
        Una vez al día."""
        if ahora - self._compactada < DIA:
            return 0
        corte = (ahora - retener) - (ahora - retener) % (15 * MIN)
        with self.lock, self._db() as db, db:
            pend = db.execute("""SELECT min(p.t_emision) FROM proyecciones p LEFT JOIN resultados_proy r
                                 ON r.simbolo=p.simbolo AND r.hora=p.hora WHERE r.hora IS NULL""").fetchone()[0]
            if pend is not None:
                corte = min(corte, pend - pend % (15 * MIN) - 15 * MIN)
            filas = db.execute("SELECT simbolo, t, o, h, l, c, v, n, fuente FROM velas_1m WHERE t<? ORDER BY simbolo, t",
                               (corte,)).fetchall()
            grupos = {}
            for s, tt, o, h, l, c, v, n, f in filas:
                k = (s, tt - tt % (15 * MIN))
                g = grupos.get(k)
                ops = (n > 0) if n is not None else (v or 0) > 0
                if not g:
                    grupos[k] = g = {"o": o, "h": None, "l": None, "c": None, "v": 0.0, "n": 0, "m": 0, "mo": 0, "f": set()}
                g["m"] += 1
                g["f"].add(f)
                g["v"] += v or 0
                g["n"] += n or 0
                if ops:
                    g["mo"] += 1
                    g["h"] = h if g["h"] is None else max(g["h"], h)
                    g["l"] = l if g["l"] is None else min(g["l"], l)
                    g["c"] = c
            db.executemany("""INSERT OR IGNORE INTO velas_15m VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                           [(s, k, g["o"], g["h"], g["l"], g["c"], g["v"], g["n"], g["m"], g["mo"], "+".join(sorted(g["f"])))
                            for (s, k), g in grupos.items()])
            db.execute("DELETE FROM velas_1m WHERE t<?", (corte,))
        self._compactada = ahora
        return len(filas)

    def _horas_apagado(self, desde, ahora):
        """Horas UTC completas, desde la primera emisión, en las que el servicio no estuvo encendido."""
        ses = self.sesiones(desde)
        out = []
        h = desde - desde % HORA + HORA
        while h + HORA <= ahora and len(out) < 24 * 400:
            if not any(i < h + HORA and lat >= h for i, lat in ses):
                out.append(h)
            h += HORA
        return out

    def detalle(self, s, hora):
        with self._db() as db:
            f = db.execute("""SELECT p.contenido, p.huella, r.estado, r.motivo, r.contenido, r.t_eval FROM proyecciones p
                              LEFT JOIN resultados_proy r ON r.simbolo=p.simbolo AND r.hora=p.hora
                              WHERE p.simbolo=? AND p.hora=?""", (s, hora)).fetchone()
        if not f:
            return None
        texto, hu, estado, motivo, rtexto, t_eval = f
        texto = desempaquetar(texto)
        em = json.loads(texto)
        m0, m1 = piso(em["t_emision"]) - 2 * HORA, piso(em["t_vence"]) + HORA
        v = self.velas(s, m0, m1)
        paso = 15 * MIN
        with self._db() as db:
            serie = {r[0]: list(r) for r in db.execute("""SELECT t, h, l, c FROM velas_15m WHERE simbolo=? AND t>=? AND t<?
                                                          AND minutos_ops>0""", (s, m0, m1))}
        for t, x in v.items():
            if not _tiene_ops(x):
                continue
            k = t - t % paso
            g = serie.get(k)
            serie[k] = [k, x["h"], x["l"], x["c"]] if not g else [k, max(g[1], x["h"]), min(g[2], x["l"]), x["c"]]
        tr = fusionar(self.tramos(s, m0, m1))
        return {"emision": em, "huella": hu, "integra": huella(texto) == hu, "estado": estado or "pendiente", "motivo": motivo,
                "resultado": json.loads(rtexto) if rtexto else None, "t_eval": t_eval,
                "serie": [serie[k] for k in sorted(serie)], "cobertura": tr, "paso_serie": paso}
