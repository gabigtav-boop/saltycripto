"""Motor de análisis de la v21: 16 señales en 5 familias, zonas de entrada/salida,
semáforo de momento, simulación de entradas, histórico sin información futura y validación.
Traducción exacta del JavaScript original (mismos umbrales, mismo orden de cálculo)."""
import math
import re

from .indicadores import (adx, atr, bw_at, clamp, ema, jsum, mid, piv, rsi_s, supertrend)

# id, minutos, peso
TFS = [("1h", 60, 2), ("4h", 240, 3), ("1d", 1440, 4), ("1w", 10080, 3)]
SHORT = ["1h", "4h"]
LONG = ["1d", "1w"]
TFW = {t[0]: t[2] for t in TFS}
CATN = {"tend": "Tendencia", "mom": "Impulso", "rev": "Extremos", "volu": "Volumen", "est": "Estructura"}
CAT = {"tend": ["ema", "e200", "st", "ichi"], "mom": ["macd", "rsim"], "rev": ["rsix", "bb", "div"],
       "volu": ["obv", "cmf", "vol"], "est": ["swing", "don"]}
CWT = {"trend": {"tend": 1.4, "mom": 1.2, "rev": .3, "volu": 1, "est": 1.2},
       "range": {"tend": .4, "mom": .6, "rev": 1.4, "volu": 1, "est": .6},
       "mix": {"tend": 1, "mom": 1, "rev": .8, "volu": 1, "est": 1}}
NAMES = {"ema": "Medias EMA", "e200": "EMA200", "st": "Supertrend", "ichi": "Ichimoku", "macd": "MACD",
         "rsim": "RSI impulso", "rsix": "RSI extremos", "bb": "Bollinger", "div": "Divergencia RSI", "obv": "OBV",
         "cmf": "Flujo CMF", "vol": "Volumen relativo", "swing": "Máx./mín.", "don": "Ruptura 20 velas"}


def truthy(x):
    """Verdad de JavaScript para números/None (0, NaN y None son falsos)."""
    return x is not None and x is not False and x == x and x != 0


def score_from(v, rg, ex=None):
    cats = {}
    W = CWT[rg]
    for c, keys in CAT.items():
        a = [v[k] for k in keys if k != ex and v.get(k) is not None]
        cats[c] = jsum(a) / len(a) if a else None
    sw = 0.0
    sv = 0.0
    for c in cats:
        if cats[c] is not None:
            sw += W[c]
            sv += W[c] * cats[c]
    return {"score": clamp(50 + 65 * sv / sw, 0, 100) if sw else None, "cats": cats}


def analyze(raw, drop=True):
    rows = raw[:-1] if drop else raw
    if len(rows) < 55:
        return None
    cl = [r["c"] for r in rows]
    n = len(cl)
    last = cl[n - 1]
    x = rows[n - 1]
    E20 = ema(cl, 20)
    E50 = ema(cl, 50)
    E200 = ema(cl, 200) if n >= 200 else None
    e20, e50 = E20[n - 1], E50[n - 1]
    e200 = E200[n - 1] if E200 else None
    m12, m26 = ema(cl, 12), ema(cl, 26)
    ml = [v - m26[i] for i, v in enumerate(m12)]
    sg = ema(ml, 9)
    hist = ml[n - 1] - sg[n - 1]
    hp = ml[n - 2] - sg[n - 2]
    RS = rsi_s(cl)
    R = RS[n - 1]
    A = atr(rows)
    AD = adx(rows)
    st = supertrend(rows)
    w = cl[-20:]
    mean = jsum(w) / 20
    sd = math.sqrt(jsum((v - mean) ** 2 for v in w) / 20)
    bbp = (last - (mean - 2 * sd)) / (4 * sd) if sd > 0 else .5
    obv = []
    o = 0.0
    for i, r in enumerate(rows):
        if i:
            o += r["v"] if r["c"] > rows[i - 1]["c"] else -r["v"] if r["c"] < rows[i - 1]["c"] else 0
            obv.append(o)
        else:
            obv.append(0)
    oe = ema(obv, 20)
    mv = 0.0
    vv = 0.0
    for r in rows[-20:]:
        h = r["h"] - r["l"]
        mv += ((r["c"] - r["l"]) - (r["h"] - r["c"])) / h * r["v"] if h > 0 else 0
        vv += r["v"]
    cmf = mv / vv if vv > 0 else 0
    vavg = jsum(r["v"] for r in rows[-21:-1]) / 20
    relvol = x["v"] / vavg if vavg > 0 else None
    ichi = None
    if n >= 80:
        e = n - 27
        sa = (mid(rows, e, 9) + mid(rows, e, 26)) / 2
        sb = mid(rows, e, 52)
        ichi = 1 if last > max(sa, sb) else -1 if last < min(sa, sb) else 0
    rr = rows[-80:]
    rs = RS[-80:]
    lo = piv(rr, "l", True)
    hi = piv(rr, "h", False)
    swing = 0
    div = 0
    if len(lo) >= 2 and len(hi) >= 2:
        HH = rr[hi[-1]]["h"] > rr[hi[-2]]["h"]
        HL = rr[lo[-1]]["l"] > rr[lo[-2]]["l"]
        swing = 1 if (HH and HL) else -1 if (not HH and not HL) else 0
    if len(lo) >= 2:
        a, b = lo[-2], lo[-1]
        if len(rr) - 1 - b <= 15 and rr[b]["l"] < rr[a]["l"] and rs[b] is not None and rs[a] is not None and rs[b] > rs[a]:
            div = 1
    if not div and len(hi) >= 2:
        a, b = hi[-2], hi[-1]
        if len(rr) - 1 - b <= 15 and rr[b]["h"] > rr[a]["h"] and rs[b] is not None and rs[a] is not None and rs[b] < rs[a]:
            div = -1
    pr = rows[-21:-1]
    don = 1 if last > max(r["h"] for r in pr) else -1 if last < min(r["l"] for r in pr) else 0
    v = {
        "ema": 1 if (last > e50 and e20 > e50) else -1 if (last < e50 and e20 < e50) else 0,
        "e200": (1 if last > e200 else -1) if e200 is not None else None,
        "st": st["dir"] if st else None,
        "ichi": ichi,
        "macd": 1 if (hist > 0 and hist > hp) else -1 if (hist < 0 and hist < hp) else 0,
        "rsim": None if R is None else 1 if 55 <= R <= 70 else -1 if 30 <= R <= 45 else 0,
        "rsix": None if R is None else 1 if R < 30 else -1 if R > 70 else 0,
        "bb": 1 if bbp < .05 else -1 if bbp > .95 else 0,
        "div": div,
        "obv": 1 if obv[n - 1] > oe[n - 1] else -1,
        "cmf": 1 if cmf > .05 else -1 if cmf < -.05 else 0,
        "vol": (1 if x["c"] >= x["o"] else -1) if (relvol is not None and relvol > 1.3) else 0,
        "swing": swing,
        "don": don,
    }
    er = None
    if n > 21:
        den = 0.0
        tail = cl[-20:]
        for i, z in enumerate(tail):
            den += abs(z - tail[i - 1]) if i else abs(z - cl[n - 21])
        er = abs(cl[n - 1] - cl[n - 21]) / (den or 1)
    if AD is not None and er is not None:
        rg = "trend" if (AD > 25 and er > .3) else "range" if (AD < 20 or er < .2) else "mix"
    else:
        rg = "mix"
    SC = score_from(v, rg)
    sq = False
    if n >= 125:
        cur = bw_at(cl, n - 1)
        c = 0
        for i in range(n - 100, n):
            if bw_at(cl, i) <= cur:
                c += 1
        sq = c <= 20
    return {"score": SC["score"], "cats": SC["cats"], "v": v, "rg": rg, "rsi": R, "adx": AD, "er": er, "atr": A,
            "atrPct": A / last * 100 if truthy(A) else None, "relvol": relvol, "last": last, "rows": rows,
            "E20": E20, "E50": E50, "e20": e20, "e50": e50, "e200": e200, "bbU": mean + 2 * sd, "bbL": mean - 2 * sd,
            "st": st, "sq": sq}


_MAJOR = re.compile(r"mayor|semanal|anual", re.I)


def zones(a, price, bk=None, wk=None):
    """Soportes y resistencias por confluencia; cuenta las visitas del precio a cada zona."""
    R = a["rows"]
    r = R[-120:]
    r90 = R[-90:]
    C = []
    lo = piv(r, "l", True)
    hi = piv(r, "h", False)
    A = a["atrPct"] if truthy(a["atrPct"]) else 3

    def add(p, t, f):
        if p is not None and p > 0 and math.isfinite(p):
            C.append((p, t, f))

    for i in lo:
        add(r[i]["l"], "Pivote mín.", "piv")
    for i in hi:
        add(r[i]["h"], "Pivote máx.", "piv")
    H = max(x["h"] for x in r90)
    L = min(x["l"] for x in r90)
    for f, t in ((.382, "Fib 38,2"), (.5, "Fib 50"), (.618, "Fib 61,8")):
        add(H - (H - L) * f, t, "fib")
    if len(R) >= 200:
        y = R[-365:]
        YH = max(x["h"] for x in y)
        YL = min(x["l"] for x in y)
        for i in piv(y, "l", True, 8):
            add(y[i]["l"], "Mínimo mayor", "piv")
        for i in piv(y, "h", False, 8):
            add(y[i]["h"], "Máximo mayor", "piv")
        add(YH, "Máx. anual", "piv")
        add(YL, "Mín. anual", "piv")
        for f, t in ((.382, "Fib anual 38,2"), (.5, "Fib anual 50"), (.618, "Fib anual 61,8")):
            add(YH - (YH - YL) * f, t, "fiby")
    if wk and wk.get("rows"):
        add(wk["e20"], "EMA20 semanal", "maw")
        add(wk["e50"], "EMA50 semanal", "maw")
        w = wk["rows"][-80:]
        for i in piv(w, "l", True, 2):
            add(w[i]["l"], "Mínimo semanal", "piv")
        for i in piv(w, "h", False, 2):
            add(w[i]["h"], "Máximo semanal", "piv")
    add(a["e20"], "EMA20", "ma")
    add(a["e50"], "EMA50", "ma")
    add(a["bbL"], "Bollinger inf.", "bb")
    add(a["bbU"], "Bollinger sup.", "bb")
    if truthy(a["e200"]):
        add(a["e200"], "EMA200", "ma200")
    if a["st"]:
        add(a["st"]["lv"], "Supertrend", "st")

    def av(i):
        p = 0.0
        v = 0.0
        for k in range(i, len(r)):
            p += (r[k]["h"] + r[k]["l"] + r[k]["c"]) / 3 * r[k]["v"]
            v += r[k]["v"]
        return p / v if v else None

    for a2 in (lo, hi):
        if a2:
            vw = av(a2[-1])
            if truthy(vw):
                add(vw, "VWAP anclado", "vwap")
    l30 = R[-30:]
    vs = jsum(x["v"] for x in l30)
    if vs > 0:
        add(jsum((x["h"] + x["l"] + x["c"]) / 3 * x["v"] for x in l30) / vs, "VWAP 30d", "vwap")
    bins = [0.0] * 30
    stp0 = (H - L) / 30 or 1
    for x in r90:
        bins[min(29, math.floor(((x["h"] + x["l"] + x["c"]) / 3 - L) / stp0))] += x["v"]
    add(L + (bins.index(max(bins)) + .5) * stp0, "Nodo de volumen", "vol")
    m = 10 ** math.floor(math.log10(price))
    stp = m / 2
    k = math.ceil(price * .75 / stp)
    while k * stp <= price * 1.25:
        add(k * stp, "Nivel redondo", "rn")
        k += 1
    if bk:
        if bk.get("wb"):
            add(bk["wb"]["p"], "Muro de compra", "ob")
        if bk.get("wa"):
            add(bk["wa"]["p"], "Muro de venta", "ob")
    C.sort(key=lambda c: c[0])
    G = []
    tol = clamp(A * .4 / 100, .008, .02)
    for c in C:
        g = G[-1] if G else None
        if g and c[0] <= g["lo"] * (1 + tol):
            g["hi"] = c[0]
            g["t"].append(c[1])
            g["f"].append(c[2])
        else:
            G.append({"lo": c[0], "hi": c[0], "t": [c[1]], "f": [c[2]]})
    th = max(.008, A / 200)

    def visits(g):
        c = 0
        pv = False
        ev = bu = bd = 0
        a2 = g["lo"] * .997
        b2 = g["hi"] * 1.003
        mm = (g["lo"] + g["hi"]) / 2
        nr = len(r)
        for i in range(nr):
            x = r[i]
            h = x["l"] <= b2 and x["h"] >= a2
            if h and not pv:
                c += 1
                if i + 5 < nr:
                    ev += 1
                    mx = 0
                    mn = 1e18
                    for kk in range(i + 1, i + 6):
                        mx = max(mx, r[kk]["h"])
                        mn = min(mn, r[kk]["l"])
                    if mx >= mm * (1 + th):
                        bu += 1
                    if mn <= mm * (1 - th):
                        bd += 1
            pv = h
        g["ev"], g["bu"], g["bd"] = ev, bu, bd
        return c

    for g in G:
        g["mj"] = any(_MAJOR.search(t) for t in g["t"])
        g["t"] = list(dict.fromkeys(g["t"]))
        g["n"] = len(set(g["f"]))
        g["mid"] = (g["lo"] + g["hi"]) / 2
        g["tc"] = visits(g)
        g["d"] = abs(g["mid"] / price - 1)
        g["s"] = g["n"] + (1 if g["tc"] >= 3 else 0) + (1 if g["tc"] >= 5 else 0) + (1 if g["mj"] else 0)
    lim = clamp(A * 7 / 100, .08, .25)
    sp = max(.01, A * .5 / 100)

    def pick(below):
        side = [g for g in G if (g["mid"] < price * .995 if below else g["mid"] > price * 1.005)]
        side.sort(key=lambda g: g["d"])
        c = [g for g in side if g["n"] >= 2 and g["d"] <= lim]
        if len(c) < 2:
            ex = [g for g in side if not any(g is q for q in c) and g["d"] <= .25 and (g["n"] >= 2 or g["mj"] or g["tc"] >= 2)]
            for g in ex:
                g["weak"] = g["n"] < 2
            c = sorted(c + ex, key=lambda g: g["d"])
        out = []
        for g in c:
            l = out[-1] if out else None
            if l and abs(g["mid"] / l["mid"] - 1) < sp:
                if g["s"] > l["s"]:
                    out[-1] = g
            else:
                out.append(g)
        if not out:
            for k2 in (1.5, 3):
                xx = price * (1 + (-1 if below else 1) * k2 * A / 100)
                out.append({"lo": xx, "hi": xx, "mid": xx, "t": ["Proyección por volatilidad"], "f": ["proj"], "n": 1,
                            "s": 0, "tc": 0, "ev": 0, "bu": 0, "bd": 0, "d": k2 * A / 100, "proj": True, "weak": True})
        return out[:3]

    return {"sup": pick(True), "res": pick(False), "h30": max(x["h"] for x in l30), "l30": min(x["l"] for x in l30)}


def _first_real(gs):
    return next((g for g in gs if not g.get("proj")), None)


def tcore(p, d, z, bL, bS, r4, ex=None):
    """Semáforo de momento: 6 condiciones de entrada y 6 de salida."""
    bk = (ex or {}).get("bk")
    A = d["atrPct"] if truthy(d["atrPct"]) else 3
    near = max(2, A) / 100
    s = _first_real(z["sup"])
    r = _first_real(z["res"])
    ar = d["atr"] if truthy(d["atr"]) else 0
    risk = p - (s["lo"] - ar) if s else None
    up = r["mid"] - p if r else None
    rr = up / risk if (risk is not None and risk > 0 and up is not None and up > 0) else None
    rsi = d["rsi"]
    B = [["Largo plazo no bajista", bL in ("AL", "NE")],
         ["Corto plazo no bajista", bS in ("AL", "NE")],
         ["Cerca de un soporte", bool(s) and (p - s["hi"]) / p <= near],
         ["RSI diario < 65", rsi is not None and rsi < 65 and not (truthy(r4) and r4 > 72)],
         ["Beneficio/riesgo ≥ 1,5", rr is not None and rr >= 1.5],
         ["Volumen y libro sin presión vendedora", d["v"]["obv"] > 0 and d["v"]["cmf"] >= 0 and not (bk and bk["imb"] < 42)]]
    S = [["Cerca de una resistencia", bool(r) and (r["lo"] - p) / p <= near],
         ["RSI ≥ 70 o divergencia bajista", rsi is not None and (rsi >= 70 or d["v"]["div"] == -1)],
         ["Corto plazo bajista", bS == "BA"],
         ["Largo plazo o Supertrend bajista", bL == "BA" or (bool(d["st"]) and d["st"]["dir"] < 0)],
         ["Volumen o libro vendedor", (d["v"]["obv"] < 0 and d["v"]["cmf"] <= 0) or bool(bk and bk["imb"] <= 38)],
         ["Más riesgo de caída que de subida", bool(s) and bool(r) and (p - s["mid"]) > (r["mid"] - p)]]
    bn = sum(1 for x in B if x[1])
    sn = sum(1 for x in S if x[1])
    bf = bn >= 5 and B[0][1]
    sf = sn >= 4
    return {"B": B, "S": S, "bn": bn, "sn": sn, "rr": rr, "inv": s["lo"] - ar if s else None, "vol": A > 6,
            "v": "C" if (bf and not sf) else "V" if (sf and not bf) else "E"}


def timing(o):
    if not (o.get("d1") and o.get("z") and truthy(o.get("price"))):
        return None
    r4 = o["res"]["4h"]["rsi"] if o["res"].get("4h") else None
    return tcore(o["price"], o["d1"], o["z"], o["bL"], o["bS"], r4, {"bk": o.get("book")})


def sim(rows, t, z, A):
    """Entrada 1 simulada: orden límite 10 días, stop bajo la zona menos 1 ATR, objetivo Salida 1, máximo 10 días."""
    E = _first_real(z["sup"])
    X = _first_real(z["res"])
    if not E or not X or not truthy(A):
        return None
    en = E["mid"]
    st = E["lo"] - A
    tg = X["mid"]
    if not (en > st and tg > en):
        return None
    k = -1
    i = t + 1
    while i <= t + 10 and i < len(rows):
        if rows[i]["l"] <= en:
            k = i
            break
        i += 1
    if k < 0:
        return {"fill": False}
    rk = en - st
    cost = .005 * en / rk
    r = None
    res = "T"
    j = k
    while j <= k + 10 and j < len(rows):
        if rows[j]["l"] <= st:
            r = -1
            res = "S"
            break
        if j > k and rows[j]["h"] >= tg:
            r = (tg - en) / rk
            res = "W"
            break
        j += 1
    if r is None:
        r = (rows[min(k + 10, len(rows) - 1)]["c"] - en) / rk
    return {"fill": True, "res": res, "r": r - cost}


def bias(s, p=None):
    """Histéresis: la lectura de la vela anterior frena los cambios por ruido."""
    if s is None:
        return "ND"
    if s >= (57 if p == "AL" else 60):
        return "AL"
    if s <= (43 if p == "BA" else 40):
        return "BA"
    return "NE"


def backtest(raw):
    """Validación retrospectiva sin información futura: cada señal usa solo velas anteriores."""
    H = 5
    rows = raw[:-1]
    m = (210 + len(rows) - H) / 2

    def mk():
        return {"n": 0, "w": 0, "r": 0.0, "n1": 0, "w1": 0}

    b, s, cb, cv = mk(), mk(), mk(), mk()
    T = {"fill": 0, "w": 0, "s": 0, "r": 0.0}
    T0 = {"fill": 0, "w": 0, "s": 0, "r": 0.0}
    SM = []
    up = tot = 0
    for t in range(210, len(rows) - H, 2):
        a = analyze(rows[max(0, t - 259):t + 1], False)
        if not a or a["score"] is None:
            continue
        f = rows[t + H]["c"] / rows[t]["c"] - 1
        h = t < m
        tot += 1
        if f > 0:
            up += 1
        SM.append({"v": a["v"], "rg": a["rg"], "f": f, "h": h})
        if a["score"] >= 60:
            b["n"] += 1
            b["r"] += f
            if f > 0:
                b["w"] += 1
            if h:
                b["n1"] += 1
                if f > 0:
                    b["w1"] += 1
        elif a["score"] <= 40:
            s["n"] += 1
            s["r"] += f
            if f < 0:
                s["w"] += 1
            if h:
                s["n1"] += 1
                if f < 0:
                    s["w1"] += 1
        q = rows[t]["c"]
        bb = bias(a["score"])
        zz = zones(a, q)
        k = tcore(q, a, zz, bb, bb, None)
        if k["v"] == "C":
            cb["n"] += 1
            cb["r"] += f
            if f > 0:
                cb["w"] += 1
        elif k["v"] == "V":
            cv["n"] += 1
            cv["r"] += f
            if f < 0:
                cv["w"] += 1
        if t <= len(rows) - 24:
            x = sim(rows, t, zz, a["atr"])
            if x and x["fill"]:
                T["fill"] += 1
                T["r"] += x["r"]
                if x["res"] == "W":
                    T["w"] += 1
                elif x["res"] == "S":
                    T["s"] += 1
            E1 = _first_real(zz["sup"])
            if E1:
                u = abs(E1["mid"] / q - 1) * (.5 + ((t * 2654435761) % 4294967296 % 1000) / 1000)
                xx = q * (1 - u)
                y = sim(rows, t, {"sup": [{"lo": xx, "hi": xx, "mid": xx}], "res": zz["res"]}, a["atr"])
                if y and y["fill"]:
                    T0["fill"] += 1
                    T0["r"] += y["r"]
                    if y["res"] == "W":
                        T0["w"] += 1
                    elif y["res"] == "S":
                        T0["s"] += 1
    if tot < 60:
        return None
    return {"b": b, "s": s, "cb": cb, "cv": cv, "T": T, "T0": T0, "SM": SM, "base": up / tot, "base2": 1 - up / tot, "tot": tot}


def gs(res, ids):
    a = 0.0
    b = 0
    for i in ids:
        if res.get(i) and res[i]["score"] is not None:
            a += res[i]["score"] * TFW[i]
            b += TFW[i]
    return a / b if b else None


def book_sum(d, price=None):
    """Resumen del libro de Kraken: presión, spread y muros (instantánea)."""
    if not d or not d.get("asks") or not d.get("bids"):
        return None
    a = [(float(x[0]), float(x[1])) for x in d["asks"]]
    b = [(float(x[0]), float(x[1])) for x in d["bids"]]
    md = (a[0][0] + b[0][0]) / 2
    spread = (a[0][0] - b[0][0]) / md * 100
    covA = a[-1][0] / md - 1
    covB = 1 - b[-1][0] / md
    lim = min(.02, covA, covB)
    if not (lim > 0.0005):
        return None
    nb = jsum(x[0] * x[1] for x in b if x[0] >= md * (1 - lim))
    na = jsum(x[0] * x[1] for x in a if x[0] <= md * (1 + lim))

    def wall(side):
        arr = b if side == "b" else a
        bk = {}
        for p, v in arr:
            dd = abs(p / md - 1)
            if dd > .08:
                continue
            k = math.floor(dd / .0025)
            bk[k] = bk.get(k, 0) + p * v
        vals = sorted(bk.values())
        med = vals[len(vals) // 2] if vals else 0
        best = None
        for k, v in bk.items():
            if med > 0 and v >= 3 * med and (not best or v > best["v"]):
                best = {"v": v, "p": md * (1 - (k + .5) * .0025) if side == "b" else md * (1 + (k + .5) * .0025)}
        return best

    return {"spread": spread, "lim": lim, "imb": nb / (nb + na) * 100 if nb + na > 0 else 50, "wb": wall("b"), "wa": wall("a")}


def src_info(o):
    a = [(n, p) for n, p in (("Kraken", o.get("price")), ("Coinbase", o.get("cbp")), ("CoinGecko", o.get("cgp"))) if p is not None and p > 0]
    v = sorted(p for _, p in a)
    med = v[len(v) // 2] if v else 0
    return {"n": len(a), "dev": max(abs(p / med - 1) for _, p in a) if med else 0, "names": [n for n, _ in a]}


def src_ok(o):
    si = src_info(o)
    kb = bias(o["d1"]["score"]) if o.get("d1") and o["d1"]["score"] is not None else None
    cbb = o.get("cbb")
    return si["n"] >= 2 and si["dev"] <= .015 and (not cbb or cbb == "NE" or kb == "NE" or cbb == kb)


def fin(o):
    """Semáforo + 6 controles de fiabilidad."""
    o["tm"] = timing(o)
    bt = o.get("bt")
    bL = o.get("bL")
    st = (bt and bt["b"]) if bL == "AL" else (bt and bt["s"]) if bL == "BA" else None
    base = (bt["base"] if bL == "AL" else bt["base2"]) if bt else 0
    stable = bool(st and st["n"] >= 15 and st["w"] / st["n"] > base + .05 and st["n1"] >= 5 and st["n"] - st["n1"] >= 5
                  and st["w1"] / st["n1"] > base and (st["w"] - st["w1"]) / (st["n"] - st["n1"]) > base)
    d1 = o.get("d1")
    reg = o.get("reg")
    o["crit"] = [
        ["Convicción diaria", bool(d1 and d1["score"] is not None and abs(d1["score"] - 50) >= 15)],
        ["Corto y largo coinciden", o.get("bS") == bL and o.get("bS") in ("AL", "BA")],
        ["Régimen coherente", (reg == "tendencia alcista" and bL == "AL") or (reg == "tendencia bajista" and bL == "BA") or (reg == "lateral" and bL == "NE")],
        ["Fuentes coinciden", src_ok(o)],
        ["3+ gráficos con datos", len(o["res"]) >= 3],
        ["Histórico con ventaja estable", stable],
    ]
    p = sum(1 for x in o["crit"] if x[1])
    o["conf"] = "Alta" if p >= 5 else "Media" if p >= 3 else "Baja"
    return o


def corr(x, y):
    n = len(x)
    if n < 30:
        return None
    mx = jsum(x) / n
    my = jsum(y) / n
    sxy = sxx = syy = 0.0
    for i in range(n):
        a = x[i] - mx
        b = y[i] - my
        sxy += a * b
        sxx += a * a
        syy += b * b
    return sxy / math.sqrt(sxx * syy) if (sxx and syy) else None


def ic_of(S, ex=None):
    x, y = [], []
    for p in S:
        sc = score_from(p["v"], p["rg"], ex)["score"]
        if sc is not None:
            x.append(sc)
            y.append(p["f"])
    return corr(x, y)


def validate(data):
    """Validación conjunta con el histórico de todas las monedas."""
    A = {"b": {"n": 0, "w": 0, "r": 0.0}, "s": {"n": 0, "w": 0, "r": 0.0}, "cb": {"n": 0, "w": 0, "r": 0.0},
         "cv": {"n": 0, "w": 0, "r": 0.0}, "T": {"fill": 0, "w": 0, "s": 0, "r": 0.0}, "T0": {"fill": 0, "w": 0, "s": 0, "r": 0.0}}
    S = []
    bs = 0.0
    bt_ = 0
    nc = 0
    for k in data:
        b = data[k].get("bt") if data[k] else None
        if not b:
            continue
        nc += 1
        for g in ("b", "s", "cb", "cv"):
            for f in ("n", "w", "r"):
                A[g][f] += b[g][f]
        for f in ("fill", "w", "s", "r"):
            A["T"][f] += b["T"][f]
            A["T0"][f] += b["T0"][f]
        S.extend(b["SM"])
        bs += b["base"] * b["tot"]
        bt_ += b["tot"]
    if not nc:
        return None
    icAll = ic_of(S)
    ic1 = ic_of([p for p in S if p["h"]])
    ic2 = ic_of([p for p in S if not p["h"]])
    abl = []
    if icAll is not None:
        for k in NAMES:
            e = ic_of(S, k)
            d = icAll - (e if e is not None else 0)  # en JS, x - null = x
            if math.isfinite(d):
                abl.append({"k": k, "d": d})
    abl.sort(key=lambda z: -z["d"])
    gt = lambda v, t: v is not None and v > t  # noqa: E731  (null > x es falso en JS)
    if icAll is None:
        vd = ["ND", "Sin datos suficientes"]
    elif icAll >= .04 and gt(ic1, .02) and gt(ic2, .02):
        vd = ["AL", "Ventaja modesta"]
    elif icAll >= .02:
        vd = ["NE", "Ventaja débil o inestable"]
    elif icAll < 0:
        vd = ["BA", "Sin ventaja (resultado inverso)"]
    else:
        vd = ["NE", "Sin ventaja demostrable"]
    return {"vd": vd, "icAll": icAll, "ic1": ic1, "ic2": ic2, "A": A, "base": bs / bt_, "abl": abl, "nc": nc}
