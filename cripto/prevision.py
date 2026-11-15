"""Previsión combinada por moneda: hacia dónde es más probable que se mueva el precio
teniendo en cuenta todos los datos, con objetivo, invalidación y el peso de cada factor.

No es una certeza: es una síntesis transparente. Cada factor se normaliza a -1..1
(negativo = bajista) y se pondera; el detalle se muestra siempre."""


def _c(x, a=-1.0, b=1.0):
    return max(a, min(b, x))


def factores(o, deriv=None, pulso=None, sent=None):
    vv = o.get("vivo") or {}
    ts = vv.get("ts", o.get("ts"))
    tl = o.get("tl")
    tm = (o.get("tm") or {}).get("v")
    mi = (o.get("micro") or {}).get("score")
    f = []

    def add(nombre, valor, peso, dato):
        if valor is not None:
            f.append({"nombre": nombre, "valor": _c(valor), "peso": peso, "dato": dato})

    add("Técnico corto plazo", None if ts is None else (ts - 50) / 35, .22, None if ts is None else f"{ts:.0f}/100 (1h y 4h en vivo)")
    add("Técnico largo plazo", None if tl is None else (tl - 50) / 35, .22, None if tl is None else f"{tl:.0f}/100 (diario y semanal)")
    if tm:
        add("Semáforo de momento", {"C": 1, "V": -1, "E": 0}[tm], .10, {"C": "compra favorable", "V": "venta favorable", "E": "esperar"}[tm])
    add("Flujo de órdenes", None if mi is None else mi / 100, .10, None if mi is None else (o.get("micro") or {}).get("lectura"))
    if pulso:
        add("Noticias 24 h", pulso["score"] / 70, .16, f"{pulso['lectura'].lower()} ({pulso['score']:+.0f}, {pulso['n']} noticias)")
    if deriv and deriv.get("ok"):
        fu, lp = deriv.get("funding"), deriv.get("largos_pct")
        parts = []
        if fu is not None:
            parts.append(-(fu - .0001) / .0002)     # funding alto = euforia apalancada (contrario)
        if lp is not None:
            parts.append(-(lp - 62) / 18)            # muchas cuentas en largo = señal contraria
        if parts:
            v = sum(_c(p) for p in parts) / len(parts)
            add("Derivados (contrario)", v, .10, f"funding {fu * 100:.4f} % · {lp:.0f} % en largo" if fu is not None and lp is not None else "")
    if sent:
        add("Sentimiento (contrario)", (50 - sent["valor"]) / 50 * .6, .10, f"{sent['lectura'].lower()} ({sent['valor']:.0f})")
    return f


def prevision(o, deriv=None, pulso=None, sent=None):
    if not o or not o.get("price") or not o.get("z"):
        return None
    f = factores(o, deriv, pulso, sent)
    if not f:
        return None
    w = sum(x["peso"] for x in f)
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
    p = o["price"]
    z = o["z"]
    sup = [g for g in z["sup"] if not g.get("proj")] or z["sup"]
    res = [g for g in z["res"] if not g.get("proj")] or z["res"]
    s1 = sup[0] if sup else None
    r1 = res[0] if res else None
    atr = ((o.get("d1") or {}).get("atr")) or 0
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
    return {"score": sc, "dir": dirc, "conf": conf, "acuerdo": acuerdo, "objetivo": obj, "invalidacion": inv,
            "factores": f, "a_favor": [x["nombre"] for x in fav[:3]], "en_contra": [x["nombre"] for x in con[:3]],
            "horizonte": "horas a pocos días"}
