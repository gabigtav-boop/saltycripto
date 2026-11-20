"""Condiciones de acumulación en caídas (spot, largo plazo). Solo analiza: no es una orden ni una probabilidad.

La MISMA función decide la señal en el estudio (`estudios/estudio11_acumulacion.py`, años de velas diarias) y en vivo, así
lo que se muestra es exactamente lo que se ha validado. Umbrales fijados ANTES de mirar los resultados (valores de manual,
sin optimizar); el estudio solo informa de la sensibilidad, no elige los mejores.

Condiciones, con la vela diaria cerrada (sin información futura):
  caida       [obligatoria] condición LITERAL de acum-1, con cierre C, máximo de 90 días M (máximo de los máximos de las
              90 velas diarias cerradas) y ATR 14 de Wilder A:      1 − C/M  ≥  K_ATR · A / C
              Es una caída porcentual frente al máximo comparada con K_ATR veces el ATR relativo al cierre (dos
              denominadores distintos). NO es la regla verbal «seis ATR por debajo del máximo», M − C ≥ K_ATR · A.
              Ejemplo: M=100, C=70, A=4 → 0,30 ≥ 0,343 es falso con acum-1; 30 ≥ 24 sería verdadero con la verbal.
              Se mantiene la histórica porque es la que se estudió (estudio 11); la verbal habría cambiado los días con
              señal (BTC de 121 a 235, ETH de 101 a 276, informe del 20 nov 2026) y exigiría otra versión y otro estudio.
  rsi         RSI diario de 14 < 30
  bollinger   cierre por debajo de la banda inferior de Bollinger (20, 2)
  capitulacion  en los 3 últimos días, una vela bajista con volumen ≥ 2 veces la mediana de 20 días
  miedo       índice de miedo y codicia ≤ 20 (miedo extremo; todo el mercado)
  funding     funding medio de 3 días < 0 (los cortos pagan a los largos: el apalancamiento comprador se ha limpiado)
Señal «capitulación técnica»: `caida` y al menos MIN_OTRAS de las demás con dato. Una condición sin dato no cuenta ni a
favor ni en contra (se dice cuántas había disponibles)."""
import math

VERSION = "acum-1"
K_ATR = 6.0
MIN_OTRAS = 2
REGLA_CAIDA = "1 − C/M ≥ 6·ATR/C (cierre C, máximo de 90 días M, ATR 14)"
NOMBRES = {"caida": "Caída fuerte desde el máximo de 90 días", "rsi": "RSI diario por debajo de 30",
           "bollinger": "Cierre bajo la banda inferior de Bollinger", "capitulacion": "Volumen de capitulación",
           "miedo": "Miedo extremo en el mercado", "funding": "Funding negativo (3 días)"}
ORDEN = tuple(NOMBRES)


def _wilder_rsi(c, p=14):
    n = len(c)
    out = [None] * n
    if n < p + 2:
        return out
    g = sum(max(c[i] - c[i - 1], 0) for i in range(1, p + 1)) / p
    l = sum(max(c[i - 1] - c[i], 0) for i in range(1, p + 1)) / p
    out[p] = 100 if l == 0 else 100 - 100 / (1 + g / l)
    for i in range(p + 1, n):
        d = c[i] - c[i - 1]
        g = (g * (p - 1) + max(d, 0)) / p
        l = (l * (p - 1) + max(-d, 0)) / p
        out[i] = 100 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def _wilder_atr(rows, p=14):
    n = len(rows)
    out = [None] * n
    if n < p + 2:
        return out
    tr = [None] + [max(rows[i]["h"] - rows[i]["l"], abs(rows[i]["h"] - rows[i - 1]["c"]), abs(rows[i]["l"] - rows[i - 1]["c"]))
                   for i in range(1, n)]
    a = sum(tr[1:p + 1]) / p
    out[p] = a
    for i in range(p + 1, n):
        a = (a * (p - 1) + tr[i]) / p
        out[i] = a
    return out


def series(rows):
    """Indicadores diarios para toda la serie (rows: dicts t,o,h,l,c,v ordenados, solo velas cerradas)."""
    c = [r["c"] for r in rows]
    rsi = _wilder_rsi(c)
    atr = _wilder_atr(rows)
    n = len(rows)
    bb, dd, cap = [None] * n, [None] * n, [False] * n
    for i in range(n):
        if i >= 19:
            w = c[i - 19:i + 1]
            m = sum(w) / 20
            sd = math.sqrt(sum((x - m) ** 2 for x in w) / 20)
            bb[i] = m - 2 * sd
        if i >= 89:
            mx = max(r["h"] for r in rows[i - 89:i + 1])
            dd[i] = c[i] / mx - 1 if mx > 0 else None
        if i >= 20:
            vs = sorted(r["v"] for r in rows[i - 20:i])
            med = (vs[9] + vs[10]) / 2
            cap[i] = bool(med > 0 and rows[i]["c"] < rows[i]["o"] and rows[i]["v"] >= 2 * med)
    return {"rsi": rsi, "atr": atr, "bb": bb, "dd": dd, "cap": cap, "c": c}


def condiciones(S, i, fng=None, funding3d=None, k_atr=K_ATR):
    """{clave: True/False/None} en el índice i. None = sin dato."""
    c = S["c"][i]
    a, d = S["atr"][i], S["dd"][i]
    out = {"caida": None if (a is None or d is None or c <= 0) else d <= -k_atr * a / c,
           "rsi": None if S["rsi"][i] is None else S["rsi"][i] < 30,
           "bollinger": None if S["bb"][i] is None else c < S["bb"][i],
           "capitulacion": None if i < 22 else any(S["cap"][j] for j in range(i - 2, i + 1)),
           "miedo": None if fng is None else fng <= 20,
           "funding": None if funding3d is None or not math.isfinite(funding3d) else funding3d < 0}
    return out


def senal(cond, min_otras=MIN_OTRAS):
    otras = [v for k, v in cond.items() if k != "caida" and v is not None]
    n = sum(1 for v in otras if v)
    activa = bool(cond.get("caida")) and n >= min_otras
    return {"activa": activa, "caida": bool(cond.get("caida")), "n_otras": n, "disponibles": len(otras),
            "cumple": sum(1 for v in cond.values() if v), "total": sum(1 for v in cond.values() if v is not None)}


def tramos(precio, zonas, atr):
    """Plan de acumulación escalonada (presentación, no validado): un tercio ahora o en la zona más cercana, otro en el
    soporte siguiente y el último más abajo; y el nivel en el que conviene revisar la tesis."""
    reales = [z for z in (zonas or []) if not z.get("proj") and z.get("mid") and z["mid"] < precio]
    reales.sort(key=lambda z: -z["mid"])
    a = atr or precio * .03
    t1 = precio
    t2 = reales[0]["mid"] if reales else precio - a
    t3 = reales[1]["mid"] if len(reales) > 1 else t2 - 1.5 * a
    if t3 >= t2:
        t3 = t2 - a
    return {"niveles": [t1, t2, t3], "revisar": t3 - a,
            "pct": [0.0, (t2 / precio - 1) * 100, (t3 / precio - 1) * 100]}


def lectura_viva(rows, fng, funding3d, zonas, precio, resumen_estudio=None):
    """Estado para la página con las velas diarias CERRADAS (como en el estudio) y el precio de ahora para los tramos."""
    if not rows or len(rows) < 92:
        return None
    S = series(rows)
    i = len(rows) - 1
    cond = condiciones(S, i, fng, funding3d)
    sg = senal(cond)
    val = (resumen_estudio or {}).get("veredicto") == "demostrada"
    if sg["activa"]:
        est = ("zona", "Zona de acumulación") if val else ("capitulacion", "Capitulación técnica · ventaja no demostrada")
    elif sg["caida"]:
        est = ("caida", "Caída fuerte, sin señales de agotamiento")
    else:
        est = ("no", "Sin condiciones de acumulación")
    a, c = S["atr"][i], S["c"][i]
    out = {"version": VERSION, "regla_caida": REGLA_CAIDA, "estado": est[0], "texto": est[1], **sg,
           "condiciones": [{"k": k, "n": NOMBRES[k], "ok": cond[k]} for k in ORDEN],
           "dd90": S["dd"][i], "atr_pct": a / c * 100 if a and c else None, "rsi": S["rsi"][i],
           "caida_atr": (-S["dd"][i] * c / a) if (a and S["dd"][i] is not None) else None, "t_vela": rows[i]["t"]}
    if sg["activa"] or sg["caida"]:
        out["tramos"] = tramos(precio or c, zonas, a)
    return out
