"""Previsión combinada por moneda: hacia dónde es más probable que se mueva el precio
teniendo en cuenta todos los datos, con objetivo, invalidación y el peso de cada factor.

No es una certeza: es una síntesis transparente. Cada factor se normaliza a -1..1
(negativo = bajista) y se pondera; el detalle se muestra siempre.

Tres cosas distintas que NO deben confundirse (la interfaz las muestra por separado):
  · intensidad: la puntuación combinada (-100..100). Mide cuánto se inclinan los factores, no una probabilidad.
  · acuerdo: qué fracción del peso disponible va en el mismo sentido que la puntuación (alta/media/baja). Tampoco es un acierto.
  · probabilidad de que el escenario ocurra: NO se calcula. Los pesos son heurísticos y nunca se calibraron contra resultados
    (ver ANALISIS_FACTORES.md y ANALISIS_PREDICCION.md): `prob_calibrada` es siempre None.

Datos ausentes: un factor sin dato no se rellena con cero ni con un valor neutro; se descarta y los pesos de los presentes se
renormalizan. Para que dos factores no parezcan tan fiables como siete, el resultado incluye la `cobertura` (fracción del peso
total que sí tenía dato) y el acuerdo se rebaja a «baja» si cubre menos de la mitad (o a «media» como mucho si cubre menos de
tres cuartas partes). Un valor no finito (NaN, infinito) o un diccionario incompleto cuentan como dato ausente."""
import math

# Ponderaciones HEURÍSTICAS: elegidas a mano, sin validar fuera de muestra. Los estudios disponibles no han podido demostrar
# que ninguna combinación acierte la dirección mejor que el azar (ANALISIS_PREDICCION.md), así que no hay base para
# cambiarlas por otras «mejores»; se conservan tal cual y se documentan como lo que son.
PESOS = {"Técnico corto plazo": .22, "Técnico largo plazo": .22, "Semáforo de momento": .10, "Flujo de órdenes": .10,
         "Noticias 24 h": .16, "Derivados (contrario)": .10, "Sentimiento (contrario)": .10}
UMBRAL_COBERTURA_BAJA = .5     # por debajo, el acuerdo nunca pasa de «baja»
UMBRAL_COBERTURA_MEDIA = .75   # por debajo, el acuerdo nunca pasa de «media»


def _c(x, a=-1.0, b=1.0):
    return max(a, min(b, x))


def _num(x):
    """Número finito o None: None, texto, booleanos, NaN e infinitos son «sin dato» (nunca una señal)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _dic(x):
    return x if isinstance(x, dict) else {}


def validar_pesos(pesos):
    """Devuelve los pesos a usar. None → los predeterminados. Un conjunto inválido (clave desconocida, negativo, no finito
    o suma cero) lanza ValueError: no se corrige en silencio."""
    if pesos is None:
        return dict(PESOS)
    if not isinstance(pesos, dict) or not pesos:
        raise ValueError("pesos: se esperaba un diccionario no vacío")
    out = dict(PESOS)
    for k, v in pesos.items():
        if k not in PESOS:
            raise ValueError(f"pesos: factor desconocido {k!r}")
        x = _num(v)
        if x is None or x < 0:
            raise ValueError(f"pesos: valor inválido para {k!r}: {v!r}")
        out[k] = x
    if sum(out.values()) <= 0:
        raise ValueError("pesos: la suma debe ser positiva")
    return out


def factores(o, deriv=None, pulso=None, sent=None, pesos=None):
    """Lista de factores con dato. Los que faltan o están corruptos se omiten (ver la nota del módulo)."""
    P = validar_pesos(pesos)
    o = _dic(o)
    vv = _dic(o.get("vivo"))
    ts = _num(vv["ts"] if "ts" in vv else o.get("ts"))
    tl = _num(o.get("tl"))
    tm = _dic(o.get("tm")).get("v")
    micro = _dic(o.get("micro"))
    mi = _num(micro.get("score"))
    f = []

    def add(nombre, valor, dato):
        if valor is not None and P[nombre] > 0:
            f.append({"nombre": nombre, "valor": _c(valor), "peso": P[nombre], "dato": dato})

    add("Técnico corto plazo", None if ts is None else (ts - 50) / 35, None if ts is None else f"{ts:.0f}/100 (1h y 4h en vivo)")
    add("Técnico largo plazo", None if tl is None else (tl - 50) / 35, None if tl is None else f"{tl:.0f}/100 (diario y semanal)")
    if tm in ("C", "V", "E"):
        add("Semáforo de momento", {"C": 1, "V": -1, "E": 0}[tm], {"C": "compra favorable", "V": "venta favorable", "E": "esperar"}[tm])
    add("Flujo de órdenes", None if mi is None else mi / 100, None if mi is None else micro.get("lectura"))
    pulso = _dic(pulso)
    ps = _num(pulso.get("score"))
    if ps is not None and pulso.get("n_lectura", 1) != 0:        # noticias sin ninguna lectura (n_lectura=0) no son una lectura neutra
        add("Noticias 24 h", ps / 70, f"{str(pulso.get('lectura') or '').lower()} ({ps:+.0f}, {pulso.get('n', '?')} noticias)")
    deriv = _dic(deriv)
    if deriv.get("ok"):
        fu, lp = _num(deriv.get("funding")), _num(deriv.get("largos_pct"))
        parts = []
        if fu is not None:
            parts.append(-(fu - .0001) / .0002)     # funding alto = euforia apalancada (contrario)
        if lp is not None:
            parts.append(-(lp - 62) / 18)            # muchas cuentas en largo = señal contraria
        if parts:
            v = sum(_c(p) for p in parts) / len(parts)
            add("Derivados (contrario)", v, f"funding {fu * 100:.4f} % · {lp:.0f} % en largo" if fu is not None and lp is not None else "")
    sent = _dic(sent)
    sv = _num(sent.get("valor"))
    if sv is not None:
        add("Sentimiento (contrario)", (50 - sv) / 50 * .6, f"{str(sent.get('lectura') or '').lower()} ({sv:.0f})")
    return f


def prevision(o, deriv=None, pulso=None, sent=None, pesos=None):
    if not isinstance(o, dict) or not isinstance(o.get("z"), dict):
        return None
    p = _num(o.get("price"))
    if p is None or p <= 0:
        return None
    f = factores(o, deriv, pulso, sent, pesos)
    if not f:
        return None
    w = sum(x["peso"] for x in f)
    if w <= 0:
        return None
    total = sum(validar_pesos(pesos).values())
    cobertura = min(1.0, w / total)
    sc = sum(x["valor"] * x["peso"] for x in f) / w * 100
    for x in f:
        x["aporte"] = x["valor"] * x["peso"] / w * 100
    signo = 1 if sc > 0 else -1
    acuerdo = sum(x["peso"] for x in f if x["valor"] * signo > .1) / w
    if sc >= 18:
        dirc = "alcista"
    elif sc <= -18:
        dirc = "bajista"
    else:
        dirc = "lateral"
    conf = "alta" if abs(sc) >= 45 and acuerdo >= .7 else "media" if abs(sc) >= 25 and acuerdo >= .5 else "baja"
    if cobertura < UMBRAL_COBERTURA_BAJA:
        conf = "baja"
    elif cobertura < UMBRAL_COBERTURA_MEDIA and conf == "alta":
        conf = "media"
    z = o["z"]
    zonas = lambda k: [g for g in (z.get(k) or []) if isinstance(g, dict) and all(_num(g.get(c)) is not None for c in ("lo", "mid", "hi"))]  # noqa: E731
    sup = [g for g in zonas("sup") if not g.get("proj")] or zonas("sup")
    res = [g for g in zonas("res") if not g.get("proj")] or zonas("res")
    s1 = sup[0] if sup else None
    r1 = res[0] if res else None
    atr = _num(_dic(o.get("d1")).get("atr")) or 0
    obj = inv = None
    if dirc == "bajista" and s1:
        obj = {"precio": s1["mid"], "pct": (s1["mid"] / p - 1) * 100, "nivel": "Entrada 1"}
        if r1:
            inv = {"precio": r1["hi"], "pct": (r1["hi"] / p - 1) * 100, "texto": "un cierre por encima de la Salida 1"}
    elif dirc == "alcista" and r1:
        obj = {"precio": r1["mid"], "pct": (r1["mid"] / p - 1) * 100, "nivel": "Salida 1"}
        if s1:
            x = s1["lo"] - atr
            inv = {"precio": x, "pct": (x / p - 1) * 100, "texto": "un cierre por debajo del soporte de la Entrada 1"}
    elif s1 and r1:
        obj = {"rango": [s1["mid"], r1["mid"]], "pct": [(s1["mid"] / p - 1) * 100, (r1["mid"] / p - 1) * 100]}
    fav = sorted([x for x in f if x["valor"] * signo > .15], key=lambda x: -abs(x["aporte"]))
    con = sorted([x for x in f if x["valor"] * signo < -.15], key=lambda x: -abs(x["aporte"]))
    presentes = {x["nombre"] for x in f}
    return {"score": sc, "dir": dirc, "conf": conf, "acuerdo": acuerdo, "objetivo": obj, "invalidacion": inv,
            "factores": f, "a_favor": [x["nombre"] for x in fav[:3]], "en_contra": [x["nombre"] for x in con[:3]],
            "horizonte": "horas a pocos días",
            # campos añadidos (la interfaz anterior los ignora): intensidad ≠ acuerdo ≠ probabilidad
            "intensidad": abs(sc), "cobertura": cobertura, "n_factores": len(f), "n_total": len(PESOS),
            "faltan": [n for n in PESOS if n not in presentes],
            "prob_calibrada": None, "calibrada": False,
            "nota": "Intensidad y acuerdo no son probabilidades: los pesos son heurísticos y no están calibrados."}
